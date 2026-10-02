"""Test the Webex devices button platform."""

from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion
import xows

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from . import set_connected

from tests.common import MockConfigEntry, snapshot_platform


@pytest.fixture
def platforms() -> list[Platform]:
    """Only set up the button platform."""
    return [Platform.BUTTON]


async def test_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """Test the button entities."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.parametrize(
    ("entity_id", "command"),
    [
        ("button.test_device_accept_call", ["Call", "Accept"]),
        ("button.test_device_reject_call", ["Call", "Reject"]),
        ("button.test_device_disconnect_call", ["Call", "Disconnect"]),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_press(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    entity_id: str,
    command: list[str],
) -> None:
    """Test pressing a button sends the right command."""
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    mock_webex_client.xcommand.assert_awaited_once_with(command)


@pytest.mark.usefixtures("init_integration")
async def test_press_error(hass: HomeAssistant, mock_webex_client: MagicMock) -> None:
    """Test a failing command raises a translated error."""
    mock_webex_client.xcommand.side_effect = xows.CommandError("No active call")

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: "button.test_device_accept_call"},
            blocking=True,
        )
    assert exc_info.value.translation_key == "command_failed"
    assert exc_info.value.translation_placeholders == {
        "action": "Call Accept",
        "host": "192.168.1.100",
        "error": "No active call",
    }


@pytest.mark.parametrize(
    "entity_id",
    [
        "button.test_device_accept_call",
        "button.test_device_reject_call",
        "button.test_device_disconnect_call",
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_availability(
    hass: HomeAssistant, mock_webex_client: MagicMock, entity_id: str
) -> None:
    """Test buttons follow the connection state of the device."""
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    await set_connected(hass, mock_webex_client, False)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    await set_connected(hass, mock_webex_client, True)
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE
