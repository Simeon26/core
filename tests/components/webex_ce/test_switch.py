"""Test the Webex devices switch platform."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion
import xows

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_ICON,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from . import send_feedback, set_connected

from tests.common import MockConfigEntry, snapshot_platform

SELF_VIEW = "switch.test_device_self_view"
PRESENTATION = "switch.test_device_presentation"
MICROPHONE_MUTE = "switch.test_device_microphone_mute"
VIDEO_MUTE = "switch.test_device_video_mute"

ALL_SWITCHES = [SELF_VIEW, PRESENTATION, MICROPHONE_MUTE, VIDEO_MUTE]


@pytest.fixture
def platforms() -> list[Platform]:
    """Only set up the switch platform."""
    return [Platform.SWITCH]


async def test_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """Test the switch entities."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.parametrize(
    ("entity_id", "service", "command", "params", "state"),
    [
        (
            SELF_VIEW,
            SERVICE_TURN_ON,
            ["Video", "Selfview", "Set"],
            {"Mode": "On"},
            STATE_ON,
        ),
        (
            SELF_VIEW,
            SERVICE_TURN_OFF,
            ["Video", "Selfview", "Set"],
            {"Mode": "Off"},
            STATE_OFF,
        ),
        (PRESENTATION, SERVICE_TURN_ON, ["Presentation", "Start"], {}, STATE_ON),
        (PRESENTATION, SERVICE_TURN_OFF, ["Presentation", "Stop"], {}, STATE_OFF),
        (
            MICROPHONE_MUTE,
            SERVICE_TURN_ON,
            ["Audio", "Microphones", "Mute"],
            {},
            STATE_ON,
        ),
        (
            MICROPHONE_MUTE,
            SERVICE_TURN_OFF,
            ["Audio", "Microphones", "Unmute"],
            {},
            STATE_OFF,
        ),
        (
            VIDEO_MUTE,
            SERVICE_TURN_ON,
            ["Video", "Input", "MainVideo", "Mute"],
            {},
            STATE_ON,
        ),
        (
            VIDEO_MUTE,
            SERVICE_TURN_OFF,
            ["Video", "Input", "MainVideo", "Unmute"],
            {},
            STATE_OFF,
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_turn_on_off(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    entity_id: str,
    service: str,
    command: list[str],
    params: dict[str, Any],
    state: str,
) -> None:
    """Test turning switches on and off sends the right command."""
    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    mock_webex_client.xcommand.assert_awaited_once_with(command, **params)
    assert hass.states.get(entity_id).state == state


@pytest.mark.parametrize(
    ("entity_id", "service", "icon"),
    [
        (MICROPHONE_MUTE, SERVICE_TURN_ON, "mdi:microphone-off"),
        (MICROPHONE_MUTE, SERVICE_TURN_OFF, "mdi:microphone"),
        (VIDEO_MUTE, SERVICE_TURN_ON, "mdi:video-off"),
        (VIDEO_MUTE, SERVICE_TURN_OFF, "mdi:video"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_mute_icons(
    hass: HomeAssistant, entity_id: str, service: str, icon: str
) -> None:
    """Test the mute switches reflect the mute state in their icon."""
    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert hass.states.get(entity_id).attributes[ATTR_ICON] == icon


@pytest.mark.parametrize("entity_id", ALL_SWITCHES)
@pytest.mark.parametrize("service", [SERVICE_TURN_ON, SERVICE_TURN_OFF])
@pytest.mark.usefixtures("init_integration")
async def test_turn_on_off_error(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    entity_id: str,
    service: str,
) -> None:
    """Test a failing command is raised and the state is unchanged."""
    mock_webex_client.xcommand.side_effect = xows.CommandError("Failed")

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
        )
    assert exc_info.value.translation_key == "command_failed"
    assert hass.states.get(entity_id).state == STATE_OFF


@pytest.mark.parametrize(
    ("entity_id", "path", "params", "state"),
    [
        (
            SELF_VIEW,
            ["Status", "Video", "Selfview", "Mode"],
            {"Status": {"Video": {"Selfview": {"Mode": "On"}}}},
            STATE_ON,
        ),
        (
            SELF_VIEW,
            ["Status", "Video", "Selfview", "Mode"],
            {"Status": {"Video": {"Selfview": {"Mode": "Off"}}}},
            STATE_OFF,
        ),
        (
            PRESENTATION,
            ["Status", "Conference", "Presentation", "Mode"],
            {"Status": {"Conference": {"Presentation": {"Mode": "Sending"}}}},
            STATE_ON,
        ),
        (
            PRESENTATION,
            ["Status", "Conference", "Presentation", "Mode"],
            {"Status": {"Conference": {"Presentation": {"Mode": "Receiving"}}}},
            STATE_ON,
        ),
        (
            PRESENTATION,
            ["Status", "Conference", "Presentation", "Mode"],
            {"Status": {"Conference": {"Presentation": {"Mode": "Off"}}}},
            STATE_OFF,
        ),
        (
            MICROPHONE_MUTE,
            ["Status", "Audio", "Microphones", "Mute"],
            {"Status": {"Audio": {"Microphones": {"Mute": "On"}}}},
            STATE_ON,
        ),
        (
            MICROPHONE_MUTE,
            ["Status", "Audio", "Microphones", "Mute"],
            {"Status": {"Audio": {"Microphones": {"Mute": "Off"}}}},
            STATE_OFF,
        ),
        (
            VIDEO_MUTE,
            ["Status", "Video", "Input", "MainVideoMute"],
            {"Status": {"Video": {"Input": {"MainVideoMute": "On"}}}},
            STATE_ON,
        ),
        (
            VIDEO_MUTE,
            ["Status", "Video", "Input", "MainVideoMute"],
            {"Status": {"Video": {"Input": {"MainVideoMute": "Off"}}}},
            STATE_OFF,
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_feedback(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    entity_id: str,
    path: list[str],
    params: dict[str, Any],
    state: str,
) -> None:
    """Test switches follow device feedback."""
    await send_feedback(hass, mock_webex_client, path, params)

    assert hass.states.get(entity_id).state == state


@pytest.mark.parametrize(
    ("entity_id", "path"),
    [
        (SELF_VIEW, ["Status", "Video", "Selfview", "Mode"]),
        (PRESENTATION, ["Status", "Conference", "Presentation", "Mode"]),
        (MICROPHONE_MUTE, ["Status", "Audio", "Microphones", "Mute"]),
        (VIDEO_MUTE, ["Status", "Video", "Input", "MainVideoMute"]),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_feedback_invalid(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    entity_id: str,
    path: list[str],
) -> None:
    """Test unexpected feedback is ignored."""
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    await send_feedback(hass, mock_webex_client, path, {"Status": "invalid"})

    assert hass.states.get(entity_id).state == STATE_ON


@pytest.mark.parametrize("entity_id", ALL_SWITCHES)
@pytest.mark.usefixtures("init_integration")
async def test_availability(
    hass: HomeAssistant, mock_webex_client: MagicMock, entity_id: str
) -> None:
    """Test switches follow the connection state of the device."""
    await set_connected(hass, mock_webex_client, False)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    await set_connected(hass, mock_webex_client, True)
    assert hass.states.get(entity_id).state == STATE_OFF
