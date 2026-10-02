"""Test the Webex devices config flow."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import xows

from homeassistant import config_entries
from homeassistant.components.webex_ce.const import DOMAIN
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import DEVICE_INFO, SERIAL, create_mock_client

from tests.common import MockConfigEntry

USER_INPUT = {
    CONF_HOST: "1.1.1.1",
    CONF_USERNAME: "test-username",
    CONF_PASSWORD: "test-password",
}

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


@pytest.fixture
def mock_flow_client() -> Generator[MagicMock]:
    """Return the mocked client used to validate the user input."""
    client = create_mock_client(DEVICE_INFO, {}, [])
    with patch(
        "homeassistant.components.webex_ce.config_flow.WebexCEClient",
        return_value=client,
    ) as client_class:
        client.client_class = client_class
        yield client


async def test_form(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_flow_client: MagicMock,
) -> None:
    """Test we get the form and can create an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Device"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == SERIAL
    assert len(mock_setup_entry.mock_calls) == 1

    mock_flow_client.client_class.assert_called_once_with(
        "1.1.1.1", "test-username", "test-password"
    )
    mock_flow_client.connect.assert_awaited_once()
    mock_flow_client.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    ("connect_error", "device_info_error", "error"),
    [
        (
            xows.AuthenticationFailure("Authentication failed", 403),
            None,
            "invalid_auth",
        ),
        (ConnectionError("Connection refused"), None, "cannot_connect"),
        (TimeoutError, None, "cannot_connect"),
        (None, xows.CommandError("No such path"), "cannot_connect"),
    ],
)
async def test_form_errors(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_flow_client: MagicMock,
    connect_error: Exception | None,
    device_info_error: Exception | None,
    error: str,
) -> None:
    """Test we handle errors and can recover from them."""
    mock_flow_client.connect.side_effect = connect_error
    mock_flow_client.get_device_info.side_effect = device_info_error

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    # The connection is closed once it was opened, even when validation fails
    assert mock_flow_client.disconnect.await_count == (
        0 if connect_error is not None else 1
    )

    mock_flow_client.connect.side_effect = None
    mock_flow_client.get_device_info.side_effect = None

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Device"
    assert result["data"] == USER_INPUT
    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_unknown_error(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_flow_client: MagicMock,
) -> None:
    """Test we handle unexpected errors during validation."""
    mock_flow_client.connect.side_effect = RuntimeError("Boom")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}

    mock_flow_client.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.usefixtures("mock_flow_client")
async def test_form_already_configured(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test we abort when the device is already configured."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(mock_setup_entry.mock_calls) == 0


async def test_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_flow_client: MagicMock,
) -> None:
    """Test the reauthentication flow updates the credentials."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"][CONF_HOST] == "192.168.1.100"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "new-username", CONF_PASSWORD: "new-password"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data == {
        CONF_HOST: "192.168.1.100",
        CONF_USERNAME: "new-username",
        CONF_PASSWORD: "new-password",
    }
    mock_flow_client.client_class.assert_called_once_with(
        "192.168.1.100", "new-username", "new-password"
    )


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (xows.AuthenticationFailure("Authentication failed", 403), "invalid_auth"),
        (ConnectionError("Connection refused"), "cannot_connect"),
        (RuntimeError("Boom"), "unknown"),
    ],
)
async def test_reauth_errors(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_flow_client: MagicMock,
    error: Exception,
    reason: str,
) -> None:
    """Test the reauthentication flow recovers from errors."""
    mock_config_entry.add_to_hass(hass)
    mock_flow_client.connect.side_effect = error

    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "new-username", CONF_PASSWORD: "new-password"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}

    mock_flow_client.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "new-username", CONF_PASSWORD: "new-password"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new-password"


async def test_reauth_wrong_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_flow_client: MagicMock,
) -> None:
    """Test the reauthentication flow rejects a different device."""
    mock_config_entry.add_to_hass(hass)
    mock_flow_client.get_device_info.return_value = replace(
        DEVICE_INFO, serial="FOC0000000"
    )

    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "new-username", CONF_PASSWORD: "new-password"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert mock_config_entry.data[CONF_PASSWORD] == "password123"
