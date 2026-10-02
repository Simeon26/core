"""Test the Webex devices select platform."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion
import xows

from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_ICON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from . import send_feedback, set_connected

from tests.common import MockConfigEntry, snapshot_platform

STANDBY = "select.test_device_standby"
PRESENTATION_SOURCE = "select.test_device_presentation_source"
CAMERA_PRESET = "select.test_device_camera_preset"

STANDBY_PATH = ["Status", "Standby"]
LOCAL_INSTANCE_PATH = ["Status", "Conference", "Presentation", "LocalInstance"]
CAMERA_PATH = ["Status", "Cameras", "Camera"]


@pytest.fixture
def platforms() -> list[Platform]:
    """Only set up the select platform."""
    return [Platform.SELECT]


async def _select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    """Select an option of a select entity."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: option},
        blocking=True,
    )


async def test_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """Test the select entities."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.parametrize(
    ("option", "command", "icon"),
    [
        ("awake", ["Standby", "Deactivate"], "mdi:monitor"),
        ("halfwake", ["Standby", "Halfwake"], "mdi:power-sleep"),
        ("sleep", ["Standby", "Activate"], "mdi:power"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_select_standby(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    option: str,
    command: list[str],
    icon: str,
) -> None:
    """Test selecting a standby state sends the right command."""
    await _select(hass, STANDBY, option)

    mock_webex_client.xcommand.assert_awaited_once_with(command)
    state = hass.states.get(STANDBY)
    assert state.state == option
    assert state.attributes[ATTR_ICON] == icon


@pytest.mark.parametrize(
    ("standby", "option", "icon"),
    [
        ("Off", "awake", "mdi:monitor"),
        ("Halfwake", "halfwake", "mdi:power-sleep"),
        ({"State": "Standby"}, "sleep", "mdi:power"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_standby_feedback(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    standby: Any,
    option: str,
    icon: str,
) -> None:
    """Test the standby state follows device feedback."""
    await send_feedback(
        hass, mock_webex_client, STANDBY_PATH, {"Status": {"Standby": standby}}
    )

    state = hass.states.get(STANDBY)
    assert state.state == option
    assert state.attributes[ATTR_ICON] == icon


@pytest.mark.parametrize(
    "params",
    [
        {"Status": {"Standby": "EnteringStandby"}},
        {"Status": {"Standby": ["Standby"]}},
        {"Status": "invalid"},
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_standby_feedback_invalid(
    hass: HomeAssistant, mock_webex_client: MagicMock, params: dict[str, Any]
) -> None:
    """Test unexpected standby feedback is ignored."""
    await send_feedback(hass, mock_webex_client, STANDBY_PATH, params)

    assert hass.states.get(STANDBY).state == STATE_UNKNOWN


@pytest.mark.usefixtures("init_integration")
async def test_select_presentation_source(
    hass: HomeAssistant, mock_webex_client: MagicMock
) -> None:
    """Test selecting a presentation source presents that connector."""
    await _select(hass, PRESENTATION_SOURCE, "2")

    mock_webex_client.xcommand.assert_awaited_once_with(
        ["Presentation", "Start"], ConnectorId=2
    )
    mock_webex_client.xset.assert_not_called()
    assert hass.states.get(PRESENTATION_SOURCE).state == "2"


def _local_instances(*instances: Any) -> dict[str, Any]:
    """Return feedback for the local presentation instances."""
    return {
        "Status": {"Conference": {"Presentation": {"LocalInstance": list(instances)}}}
    }


@pytest.mark.usefixtures("init_integration")
async def test_presentation_source_feedback(
    hass: HomeAssistant, mock_webex_client: MagicMock
) -> None:
    """Test the presentation source follows the local presentation."""
    await send_feedback(
        hass,
        mock_webex_client,
        LOCAL_INSTANCE_PATH,
        _local_instances(
            "invalid",
            {"Source": "4"},
            {"id": 1, "SendingMode": "LocalRemote", "Source": "3"},
        ),
    )
    assert hass.states.get(PRESENTATION_SOURCE).state == "3"

    # A stopped presentation is reported as a ghost instance
    await send_feedback(
        hass,
        mock_webex_client,
        LOCAL_INSTANCE_PATH,
        _local_instances({"id": 1, "ghost": "True"}),
    )
    assert hass.states.get(PRESENTATION_SOURCE).state == STATE_UNKNOWN


@pytest.mark.parametrize(
    "params",
    [
        _local_instances({"id": 1, "Source": "9"}),
        {"Status": {"Conference": {"Presentation": {"LocalInstance": {"id": 1}}}}},
        {"Status": "invalid"},
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_presentation_source_feedback_invalid(
    hass: HomeAssistant, mock_webex_client: MagicMock, params: dict[str, Any]
) -> None:
    """Test unexpected presentation source feedback is ignored."""
    await send_feedback(hass, mock_webex_client, LOCAL_INSTANCE_PATH, params)

    assert hass.states.get(PRESENTATION_SOURCE).state == STATE_UNKNOWN


@pytest.mark.usefixtures("init_integration")
async def test_select_camera_preset(
    hass: HomeAssistant, mock_webex_client: MagicMock
) -> None:
    """Test selecting a camera preset activates it."""
    await _select(hass, CAMERA_PRESET, "5")

    mock_webex_client.xcommand.assert_awaited_once_with(
        ["Camera", "Preset", "Activate"], PresetId=5
    )
    assert hass.states.get(CAMERA_PRESET).state == "5"


@pytest.mark.usefixtures("init_integration")
async def test_camera_preset_feedback(
    hass: HomeAssistant, mock_webex_client: MagicMock
) -> None:
    """Test the camera preset follows device feedback."""
    await send_feedback(
        hass,
        mock_webex_client,
        CAMERA_PATH,
        {
            "Status": {
                "Cameras": {
                    "Camera": [
                        "invalid",
                        {"Position": "invalid"},
                        {"Position": {}},
                        {"Position": {"ActivePreset": "4"}},
                    ]
                }
            }
        },
    )

    assert hass.states.get(CAMERA_PRESET).state == "4"


@pytest.mark.parametrize(
    "params",
    [
        {"Status": {"Cameras": {"Camera": {"Position": {"ActivePreset": "4"}}}}},
        {"Status": {"Cameras": {"Camera": [{"Position": {"ActivePreset": "99"}}]}}},
        {"Status": "invalid"},
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_camera_preset_feedback_invalid(
    hass: HomeAssistant, mock_webex_client: MagicMock, params: dict[str, Any]
) -> None:
    """Test unexpected camera preset feedback is ignored."""
    await send_feedback(hass, mock_webex_client, CAMERA_PATH, params)

    assert hass.states.get(CAMERA_PRESET).state == STATE_UNKNOWN


@pytest.mark.parametrize(
    ("entity_id", "option"),
    [(STANDBY, "sleep"), (PRESENTATION_SOURCE, "1"), (CAMERA_PRESET, "1")],
)
@pytest.mark.usefixtures("init_integration")
async def test_select_error(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    entity_id: str,
    option: str,
) -> None:
    """Test a failing command is raised and the state is unchanged."""
    mock_webex_client.xcommand.side_effect = xows.CommandError("Failed")

    with pytest.raises(HomeAssistantError) as exc_info:
        await _select(hass, entity_id, option)
    assert exc_info.value.translation_key == "command_failed"
    assert hass.states.get(entity_id).state == STATE_UNKNOWN


@pytest.mark.parametrize("entity_id", [STANDBY, PRESENTATION_SOURCE, CAMERA_PRESET])
@pytest.mark.usefixtures("init_integration")
async def test_availability(
    hass: HomeAssistant, mock_webex_client: MagicMock, entity_id: str
) -> None:
    """Test selects follow the connection state of the device."""
    await set_connected(hass, mock_webex_client, False)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    await set_connected(hass, mock_webex_client, True)
    assert hass.states.get(entity_id).state == STATE_UNKNOWN
