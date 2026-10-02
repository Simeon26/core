"""Test the Webex devices number platform."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion
import xows

from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
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

ENTITY_ID = "number.test_device_volume"
VOLUME_PATH = ["Status", "Audio", "Volume"]


@pytest.fixture
def platforms() -> list[Platform]:
    """Only set up the number platform."""
    return [Platform.NUMBER]


async def test_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """Test the number entities."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.parametrize(
    ("value", "icon"),
    [
        (0, "mdi:volume-off"),
        (20, "mdi:volume-low"),
        (50, "mdi:volume-medium"),
        (100, "mdi:volume-high"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_set_volume(
    hass: HomeAssistant, mock_webex_client: MagicMock, value: int, icon: str
) -> None:
    """Test setting the volume sends the command and updates the state."""
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_VALUE: value},
        blocking=True,
    )

    mock_webex_client.xcommand.assert_awaited_once_with(
        ["Audio", "Volume", "Set"], Level=value
    )
    state = hass.states.get(ENTITY_ID)
    assert float(state.state) == value
    assert state.attributes[ATTR_ICON] == icon


@pytest.mark.usefixtures("init_integration")
async def test_set_volume_error(
    hass: HomeAssistant, mock_webex_client: MagicMock
) -> None:
    """Test a failing volume command is raised and the state is unchanged."""
    mock_webex_client.xcommand.side_effect = xows.CommandError("Failed")

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: ENTITY_ID, ATTR_VALUE: 50},
            blocking=True,
        )
    assert exc_info.value.translation_key == "command_failed"
    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN


@pytest.mark.parametrize(
    ("volume", "expected", "icon"),
    [
        (0, 0, "mdi:volume-off"),
        ("25", 25, "mdi:volume-low"),
        ({"Value": "60"}, 60, "mdi:volume-medium"),
        ({"value": 70}, 70, "mdi:volume-high"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_volume_feedback(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    volume: Any,
    expected: int,
    icon: str,
) -> None:
    """Test the volume follows device feedback."""
    await send_feedback(
        hass, mock_webex_client, VOLUME_PATH, {"Status": {"Audio": {"Volume": volume}}}
    )

    state = hass.states.get(ENTITY_ID)
    assert float(state.state) == expected
    assert state.attributes[ATTR_ICON] == icon


@pytest.mark.parametrize(
    "params",
    [
        {"Status": {"Audio": {}}},
        {"Status": {"Audio": {"Volume": "loud"}}},
        {"Status": {"Audio": "invalid"}},
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_volume_feedback_invalid(
    hass: HomeAssistant, mock_webex_client: MagicMock, params: dict[str, Any]
) -> None:
    """Test unexpected volume feedback is ignored."""
    await send_feedback(hass, mock_webex_client, VOLUME_PATH, params)

    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN


@pytest.mark.usefixtures("init_integration")
async def test_availability(hass: HomeAssistant, mock_webex_client: MagicMock) -> None:
    """Test the volume follows the connection state of the device."""
    await set_connected(hass, mock_webex_client, False)
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE

    await set_connected(hass, mock_webex_client, True)
    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN
