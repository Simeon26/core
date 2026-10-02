"""Test the Webex devices integration setup and service actions."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol
import xows

from homeassistant.components.webex_ce.const import DOMAIN
from homeassistant.components.webex_ce.services import SCHEMAS
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .conftest import DEVICE_INFO, SERIAL, FakeXoWS, create_mock_client

from tests.common import MockConfigEntry

CALL_STATUS = "sensor.test_device_call_status"
VOLUME = "number.test_device_volume"
TARGET = {ATTR_ENTITY_ID: CALL_STATUS}


async def _wait_for(hass: HomeAssistant, condition: Callable[[], bool]) -> None:
    """Let the connection task run until a condition is met."""
    for _ in range(1000):
        await hass.async_block_till_done()
        if condition():
            return
        await asyncio.sleep(0.001)
    pytest.fail("Condition was not met")


async def test_setup_and_unload_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webex_client: MagicMock,
) -> None:
    """Test setting up and unloading a config entry."""
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data.client is mock_webex_client
    assert mock_config_entry.runtime_data.serial == SERIAL
    mock_webex_client.connect.assert_awaited_once()
    mock_webex_client.get_device_info.assert_awaited_once()
    mock_webex_client.async_maintain_connection.assert_awaited_once()

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_webex_client.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("Connection failed"),
        TimeoutError,
        xows.NotEnabledError("Not enabled", 404),
    ],
)
async def test_setup_entry_connection_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webex_client: MagicMock,
    error: Exception,
) -> None:
    """Test setup is retried when the device cannot be reached."""
    mock_config_entry.add_to_hass(hass)
    mock_webex_client.connect.side_effect = error

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_entry_device_info_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webex_client: MagicMock,
) -> None:
    """Test setup is retried when the device information cannot be read."""
    mock_config_entry.add_to_hass(hass)
    mock_webex_client.get_device_info.side_effect = TimeoutError

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_webex_client.disconnect.assert_awaited_once()


async def test_setup_entry_authentication_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webex_client: MagicMock,
) -> None:
    """Test rejected credentials start a reauthentication flow."""
    mock_config_entry.add_to_hass(hass)
    mock_webex_client.connect.side_effect = xows.AuthenticationFailure(
        "Authentication failed", 403
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert flows[0]["context"]["entry_id"] == mock_config_entry.entry_id


async def test_reconnect_authentication_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webex_client: MagicMock,
) -> None:
    """Test credentials rejected while reconnecting start a reauthentication flow."""
    mock_webex_client.async_maintain_connection.side_effect = (
        xows.AuthenticationFailure("Authentication failed", 403)
    )
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_connection_lost_and_restored(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_xows: FakeXoWS,
) -> None:
    """Test entities become unavailable while the connection is lost."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(VOLUME).state != STATE_UNAVAILABLE
    first = mock_xows.latest
    subscriptions = list(first.subscriptions)
    assert subscriptions

    # The device closes the connection and does not accept a new one yet
    mock_xows.connect_errors.append(ConnectionError("Refused"))
    with patch("homeassistant.components.webex_ce.client.RECONNECT_MIN_DELAY", 0.05):
        first.close()
        await _wait_for(
            hass, lambda: hass.states.get(VOLUME).state == STATE_UNAVAILABLE
        )
        assert hass.states.get(CALL_STATUS).state == STATE_UNAVAILABLE

        # The device accepts the next connection
        await _wait_for(
            hass, lambda: hass.states.get(VOLUME).state != STATE_UNAVAILABLE
        )

    assert len(mock_xows.clients) == 3
    assert mock_xows.latest.subscriptions == subscriptions
    assert hass.states.get(CALL_STATUS).state == "idle"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.usefixtures("init_integration")
async def test_services_registered(hass: HomeAssistant) -> None:
    """Test all documented service actions are registered."""
    assert set(hass.services.async_services_for_domain(DOMAIN)) == set(SCHEMAS)


@pytest.mark.parametrize(
    ("service", "data", "path", "params"),
    [
        ("dial", {"number": "1234567890"}, ["Dial"], {"Number": "1234567890"}),
        (
            "send_dtmf",
            {"dtmf": "123"},
            ["Call", "DTMFSend"],
            {"DTMFString": "123"},
        ),
        (
            "camera_preset_activate",
            {"preset_id": 3},
            ["Camera", "Preset", "Activate"],
            {"PresetId": 3},
        ),
        (
            "camera_preset_store",
            {"preset_id": 2},
            ["Camera", "Preset", "Store"],
            {"CameraId": 1, "PresetId": 2},
        ),
        (
            "camera_preset_store",
            {"preset_id": 2, "camera_id": 2, "name": "Whiteboard"},
            ["Camera", "Preset", "Store"],
            {"CameraId": 2, "PresetId": 2, "Name": "Whiteboard"},
        ),
        (
            "camera_position_set",
            {"pan": 100, "tilt": -50, "zoom": 2000},
            ["Camera", "PositionSet"],
            {"CameraId": 1, "Pan": 100, "Tilt": -50, "Zoom": 2000},
        ),
        (
            "camera_position_set",
            {"camera_id": 2, "zoom": 10},
            ["Camera", "PositionSet"],
            {"CameraId": 2, "Zoom": 10},
        ),
        (
            "camera_ramp",
            {"direction": "Up"},
            ["Camera", "Ramp"],
            {"CameraId": 1, "Tilt": "Up", "TiltSpeed": 5},
        ),
        (
            "camera_ramp",
            {"direction": "Left", "speed": 10, "camera_id": 2},
            ["Camera", "Ramp"],
            {"CameraId": 2, "Pan": "Left", "PanSpeed": 10},
        ),
        (
            "camera_ramp",
            {"direction": "ZoomOut", "speed": 1},
            ["Camera", "Ramp"],
            {"CameraId": 1, "Zoom": "Out", "ZoomSpeed": 1},
        ),
        (
            "camera_ramp_stop",
            {},
            ["Camera", "Ramp"],
            {"CameraId": 1, "Pan": "Stop", "Tilt": "Stop", "Zoom": "Stop"},
        ),
        (
            "display_message",
            {"text": "Hello"},
            ["UserInterface", "Message", "TextLine", "Display"],
            {"Text": "Hello", "Duration": 10},
        ),
        (
            "display_message",
            {"text": "Hello", "duration": 0},
            ["UserInterface", "Message", "TextLine", "Display"],
            {"Text": "Hello", "Duration": 0},
        ),
        (
            "clear_message",
            {},
            ["UserInterface", "Message", "TextLine", "Clear"],
            {},
        ),
        (
            "display_webview",
            {"url": "https://example.com"},
            ["UserInterface", "WebView", "Display"],
            {"Url": "https://example.com", "Mode": "Modal"},
        ),
        (
            "display_webview",
            {"url": "https://example.com", "title": "Page", "mode": "Fullscreen"},
            ["UserInterface", "WebView", "Display"],
            {"Url": "https://example.com", "Mode": "Fullscreen", "Title": "Page"},
        ),
        ("close_webview", {}, ["UserInterface", "WebView", "Clear"], {}),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_service_actions(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    service: str,
    data: dict[str, Any],
    path: list[str],
    params: dict[str, Any],
) -> None:
    """Test each service action sends the right xAPI command."""
    mock_webex_client.xcommand.reset_mock()

    await hass.services.async_call(DOMAIN, service, {**TARGET, **data}, blocking=True)

    mock_webex_client.xcommand.assert_awaited_once_with(path, **params)


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("camera_ramp", {"direction": "Sideways"}),
        ("camera_ramp", {"direction": "Up", "speed": 16}),
        ("camera_preset_activate", {"preset_id": 36}),
        ("display_webview", {"url": "https://example.com", "mode": "Popup"}),
        ("dial", {}),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_service_action_invalid_data(
    hass: HomeAssistant, service: str, data: dict[str, Any]
) -> None:
    """Test service actions validate their input."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, service, {**TARGET, **data}, blocking=True
        )


@pytest.mark.usefixtures("init_integration")
async def test_service_action_requires_target(hass: HomeAssistant) -> None:
    """Test service actions require a target."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "dial", {"number": "1234"}, blocking=True
        )


@pytest.mark.usefixtures("init_integration")
async def test_service_action_unknown_target(hass: HomeAssistant) -> None:
    """Test service actions reject targets that are not Webex devices."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "dial",
            {ATTR_ENTITY_ID: "sensor.not_a_webex_device", "number": "1234"},
            blocking=True,
        )
    assert exc_info.value.translation_key == "invalid_target"


async def test_service_action_unloaded_entry(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_webex_client: MagicMock,
) -> None:
    """Test service actions reject devices that are not loaded."""
    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    mock_webex_client.xcommand.reset_mock()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "dial", {**TARGET, "number": "1234"}, blocking=True
        )
    mock_webex_client.xcommand.assert_not_called()


@pytest.mark.usefixtures("init_integration")
async def test_service_action_failure(
    hass: HomeAssistant, mock_webex_client: MagicMock
) -> None:
    """Test a failing xAPI command raises a HomeAssistantError."""
    mock_webex_client.xcommand.side_effect = xows.CommandError("Command failed")

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN, "dial", {**TARGET, "number": "1234"}, blocking=True
        )
    assert exc_info.value.translation_key == "command_failed"
    assert exc_info.value.translation_placeholders == {
        "action": "dial",
        "host": "192.168.1.100",
        "error": "Command failed",
    }


async def test_service_action_targets_selected_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test service actions only run on the targeted device."""
    first = create_mock_client(DEVICE_INFO, {}, [])
    second = create_mock_client(
        replace(DEVICE_INFO, name="Second Device", serial="FOC0000000"), {}, []
    )
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Second Device",
        data={
            CONF_HOST: "192.168.1.101",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password123",
        },
        unique_id="FOC0000000",
    )
    mock_config_entry.add_to_hass(hass)
    second_entry.add_to_hass(hass)

    clients = {"192.168.1.100": first, "192.168.1.101": second}
    with patch(
        "homeassistant.components.webex_ce.WebexCEClient",
        side_effect=lambda host, username, password: clients[host],
    ):
        # Setting up the integration sets up both config entries
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert second_entry.state is ConfigEntryState.LOADED

    first.xcommand.reset_mock()
    second.xcommand.reset_mock()

    await hass.services.async_call(
        DOMAIN,
        "dial",
        {ATTR_ENTITY_ID: "sensor.second_device_call_status", "number": "1234"},
        blocking=True,
    )
    first.xcommand.assert_not_called()
    second.xcommand.assert_awaited_once_with(["Dial"], Number="1234")

    await hass.services.async_call(
        DOMAIN,
        "clear_message",
        {
            ATTR_ENTITY_ID: [
                "sensor.test_device_call_status",
                "sensor.second_device_call_status",
            ]
        },
        blocking=True,
    )
    first.xcommand.assert_awaited_once_with(
        ["UserInterface", "Message", "TextLine", "Clear"]
    )
    assert second.xcommand.await_count == 2


async def test_service_action_failure_multiple_devices(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test every device that fails a service action is reported."""
    first = create_mock_client(DEVICE_INFO, {}, [])
    second = create_mock_client(
        replace(DEVICE_INFO, name="Second Device", serial="FOC0000000"), {}, []
    )
    second.host = "192.168.1.101"
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Second Device",
        data={
            CONF_HOST: "192.168.1.101",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password123",
        },
        unique_id="FOC0000000",
    )
    mock_config_entry.add_to_hass(hass)
    second_entry.add_to_hass(hass)

    clients = {"192.168.1.100": first, "192.168.1.101": second}
    with patch(
        "homeassistant.components.webex_ce.WebexCEClient",
        side_effect=lambda host, username, password: clients[host],
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    first.xcommand.side_effect = xows.CommandError("No active call")
    second.xcommand.side_effect = TimeoutError()
    targets = {
        ATTR_ENTITY_ID: [CALL_STATUS, "sensor.second_device_call_status"],
        "dtmf": "1",
    }

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(DOMAIN, "send_dtmf", targets, blocking=True)
    assert exc_info.value.translation_placeholders == {
        "action": "send_dtmf",
        "host": "192.168.1.100, 192.168.1.101",
        "error": "No active call; TimeoutError",
    }

    # Errors that are not caused by the device are raised as they are
    second.xcommand.side_effect = KeyError("bug")
    with pytest.raises(KeyError):
        await hass.services.async_call(DOMAIN, "send_dtmf", targets, blocking=True)
