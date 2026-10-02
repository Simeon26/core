"""Test the Webex devices binary sensor platform."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.webex_ce.const import DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import send_feedback, set_connected
from .conftest import SERIAL

from tests.common import MockConfigEntry, snapshot_platform

RECORDING = "binary_sensor.test_device_recording"
STREAMING = "binary_sensor.test_device_streaming"


@pytest.fixture
def platforms() -> list[Platform]:
    """Only set up the binary sensor platform."""
    return [Platform.BINARY_SENSOR]


async def test_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """Test the binary sensor entities."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.parametrize(
    ("entity_id", "area", "active"),
    [
        (RECORDING, "Recording", "Recording"),
        (RECORDING, "Recording", "Active"),
        (STREAMING, "Streaming", "Streaming"),
        (STREAMING, "Streaming", "Active"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_activity_feedback(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    entity_id: str,
    area: str,
    active: str,
) -> None:
    """Test the recording and streaming sensors follow device feedback."""
    status_path = ["Status", area, "Status"]
    duration_path = ["Status", area, "Duration"]
    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF
    assert state.attributes["duration"] is None

    await send_feedback(
        hass, mock_webex_client, status_path, {"Status": {area: {"Status": active}}}
    )
    await send_feedback(
        hass, mock_webex_client, duration_path, {"Status": {area: {"Duration": "30"}}}
    )
    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
    assert state.attributes["duration"] == 30

    await send_feedback(
        hass, mock_webex_client, status_path, {"Status": {area: {"Status": "Idle"}}}
    )
    await send_feedback(hass, mock_webex_client, duration_path, {"Status": {area: {}}})
    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF
    assert state.attributes["duration"] == 0


@pytest.mark.parametrize(
    ("entity_id", "area"),
    [(RECORDING, "Recording"), (STREAMING, "Streaming")],
)
@pytest.mark.parametrize(
    ("key", "value"),
    [("Status", "invalid"), ("Duration", "invalid"), ("Duration", {"Duration": "x"})],
)
@pytest.mark.usefixtures("init_integration")
async def test_activity_feedback_invalid(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    entity_id: str,
    area: str,
    key: str,
    value: Any,
) -> None:
    """Test unexpected feedback is ignored."""
    await send_feedback(
        hass,
        mock_webex_client,
        ["Status", area, "Status"],
        {"Status": {area: {"Status": "Active"}}},
    )

    await send_feedback(
        hass, mock_webex_client, ["Status", area, key], {"Status": {area: value}}
    )

    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
    assert state.attributes["duration"] is None


@pytest.mark.parametrize("entity_id", [RECORDING, STREAMING])
@pytest.mark.usefixtures("init_integration")
async def test_availability(
    hass: HomeAssistant, mock_webex_client: MagicMock, entity_id: str
) -> None:
    """Test binary sensors follow the connection state of the device."""
    await set_connected(hass, mock_webex_client, False)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    await set_connected(hass, mock_webex_client, True)
    assert hass.states.get(entity_id).state == STATE_OFF


@pytest.mark.usefixtures("mock_webex_client")
async def test_replaced_sensors_removed(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the recording and streaming sensors are replaced."""
    mock_config_entry.add_to_hass(hass)
    for key in ("recording", "streaming"):
        entity_registry.async_get_or_create(
            SENSOR_DOMAIN,
            DOMAIN,
            f"{SERIAL}_{key}",
            config_entry=mock_config_entry,
        )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    for key in ("recording", "streaming"):
        assert not entity_registry.async_get_entity_id(
            SENSOR_DOMAIN, DOMAIN, f"{SERIAL}_{key}"
        )
        assert entity_registry.async_get_entity_id(
            BINARY_SENSOR_DOMAIN, DOMAIN, f"{SERIAL}_{key}"
        )
