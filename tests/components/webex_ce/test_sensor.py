"""Test the Webex devices sensor platform."""

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from syrupy.assertion import SnapshotAssertion
import xows

from homeassistant.const import ATTR_ICON, STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import send_feedback, set_connected

from tests.common import MockConfigEntry, snapshot_platform

CALL_STATUS = "sensor.test_device_call_status"
CURRENT_MEETING = "sensor.test_device_current_meeting"
NEXT_MEETING = "sensor.test_device_next_meeting"
ROOM_OCCUPANCY = "sensor.test_device_room_occupancy"
SOUND_LEVEL = "sensor.test_device_sound_level"
AMBIENT_NOISE = "sensor.test_device_ambient_noise"
TEMPERATURE = "sensor.test_device_ambient_temperature"
HUMIDITY = "sensor.test_device_relative_humidity"
NETWORK_STATUS = "sensor.test_device_network_status"
SYSTEM_UPTIME = "sensor.test_device_system_uptime"

CALL_PATH = ["Status", "Call"]
CONFERENCE_PATH = ["Status", "Conference"]
BOOKINGS_PATH = ["Status", "Bookings"]
NETWORK_PATH = ["Status", "Network"]

NOW = "2025-01-27T10:15:00+00:00"

ACTIVE_MEETING = {
    "Status": {
        "Conference": {
            "ActiveMeeting": {"Name": "Team Meeting", "Id": "123456789"},
            "Call": [{"id": "1"}],
            "Participants": {"Count": "3"},
            "Presentation": {"Mode": "Off"},
        }
    }
}


@pytest.fixture
def platforms() -> list[Platform]:
    """Only set up the sensor platform."""
    return [Platform.SENSOR]


@pytest.mark.freeze_time(NOW)
async def test_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """Test the sensor entities."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.parametrize(
    ("call", "state", "icon"),
    [
        ({"Status": "Connected"}, "connected", "mdi:phone-in-talk"),
        ({"Status": "Ringing"}, "ringing", "mdi:phone-ring"),
        ({"Status": "Alerting"}, "ringing", "mdi:phone-ring"),
        ({"Status": "Dialling"}, "connecting", "mdi:phone-clock"),
        ({"Status": "EarlyMedia"}, "connecting", "mdi:phone-clock"),
        ({"Status": "Connecting"}, "connecting", "mdi:phone-clock"),
        ({"Status": "Dialing"}, "connecting", "mdi:phone-clock"),
        ({"Status": "Disconnecting"}, "disconnecting", "mdi:phone-clock"),
        ({"Status": "OnHold"}, "on_hold", "mdi:phone-paused"),
        (
            {"Status": "OnHold", "AnswerState": "Answered"},
            "on_hold",
            "mdi:phone-paused",
        ),
        (
            {"Status": "Disconnecting", "AnswerState": "Answered"},
            "disconnecting",
            "mdi:phone-clock",
        ),
        ({"Status": "Preserved"}, STATE_UNKNOWN, "mdi:phone-off"),
        ({"Status": ""}, "idle", "mdi:phone-off"),
        ({"AnswerState": "Answered"}, "connected", "mdi:phone-in-talk"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_call_status(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    xget_responses: dict[tuple[str, ...], Any],
    call: dict[str, Any],
    state: str,
    icon: str,
) -> None:
    """Test the call status follows the active call."""
    xget_responses[tuple(CALL_PATH)] = [call]

    await send_feedback(hass, mock_webex_client, CALL_PATH, {"Status": {"Call": {}}})

    mock_webex_client.xget.assert_any_await(CALL_PATH)
    entity_state = hass.states.get(CALL_STATUS)
    assert entity_state.state == state
    assert entity_state.attributes[ATTR_ICON] == icon


@pytest.mark.usefixtures("init_integration")
async def test_call_status_attributes(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    xget_responses: dict[tuple[str, ...], Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the call status exposes call details and the call duration."""
    xget_responses[tuple(CALL_PATH)] = {
        "Status": "Connected",
        "AnswerState": "Answered",
        "Direction": "Incoming",
        "DisplayName": "John Doe",
        "RemoteNumber": "+1234567890",
        "CallbackNumber": "sip:john@example.com",
        "Protocol": "Sip",
        "CallType": "Video",
        "CallId": "4",
    }

    await send_feedback(hass, mock_webex_client, CALL_PATH, {})

    state = hass.states.get(CALL_STATUS)
    assert state.state == "connected"
    assert state.attributes["display_name"] == "John Doe"
    assert state.attributes["remote_number"] == "+1234567890"
    assert state.attributes["callback_number"] == "sip:john@example.com"
    assert state.attributes["protocol"] == "Sip"
    assert state.attributes["call_type"] == "Video"
    assert state.attributes["call_id"] == "4"
    assert state.attributes["direction"] == "incoming"
    assert state.attributes["duration_seconds"] == 0

    freezer.tick(timedelta(seconds=90))
    await send_feedback(hass, mock_webex_client, CALL_PATH, {})

    state = hass.states.get(CALL_STATUS)
    assert state.attributes["duration"] == "0:01:30"
    assert state.attributes["duration_seconds"] == 90

    # A held call keeps its call timer
    xget_responses[tuple(CALL_PATH)] = {"Status": "OnHold", "AnswerState": "Answered"}
    await send_feedback(hass, mock_webex_client, CALL_PATH, {})

    state = hass.states.get(CALL_STATUS)
    assert state.state == "on_hold"
    assert state.attributes["duration_seconds"] == 90

    # A call that is no longer answered resets the call timer
    xget_responses[tuple(CALL_PATH)] = {"Status": "OnHold", "AnswerState": "Unanswered"}
    await send_feedback(hass, mock_webex_client, CALL_PATH, {})

    state = hass.states.get(CALL_STATUS)
    assert state.state == "on_hold"
    assert "duration" not in state.attributes


@pytest.mark.usefixtures("init_integration")
async def test_call_status_conference_transition(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    xget_responses: dict[tuple[str, ...], Any],
) -> None:
    """Test the call status stays connected while a meeting is active.

    When a device joins a Webex meeting, the Call status may become empty while
    the Conference status indicates an active meeting. The sensor should remain
    connected and not transition to idle.
    """
    xget_responses[tuple(CALL_PATH)] = [
        {"Status": "Connected", "AnswerState": "Answered", "Direction": "Outgoing"}
    ]
    await send_feedback(hass, mock_webex_client, CALL_PATH, {})
    assert hass.states.get(CALL_STATUS).state == "connected"

    # The call disappears, but the device reports an active meeting
    xget_responses[tuple(CALL_PATH)] = []
    xget_responses[tuple(CONFERENCE_PATH)] = ACTIVE_MEETING["Status"]["Conference"]
    await send_feedback(hass, mock_webex_client, CALL_PATH, {})
    mock_webex_client.xget.assert_any_await(CONFERENCE_PATH)

    state = hass.states.get(CALL_STATUS)
    assert state.state == "connected"
    assert state.attributes["meeting_name"] == "Team Meeting"
    assert state.attributes["meeting_id"] == "123456789"
    assert state.attributes["participants_count"] == "3"
    assert state.attributes["presentation_mode"] == "Off"
    assert "duration_seconds" in state.attributes

    # The meeting ends
    await send_feedback(
        hass, mock_webex_client, CONFERENCE_PATH, {"Status": {"Conference": {}}}
    )
    assert hass.states.get(CALL_STATUS).state == "idle"


@pytest.mark.usefixtures("init_integration")
async def test_call_status_conference_feedback(
    hass: HomeAssistant, mock_webex_client: MagicMock
) -> None:
    """Test conference feedback without call data marks the call connected."""
    await send_feedback(hass, mock_webex_client, CONFERENCE_PATH, ACTIVE_MEETING)
    assert hass.states.get(CALL_STATUS).state == "connected"

    # Unexpected conference feedback is ignored
    await send_feedback(hass, mock_webex_client, CONFERENCE_PATH, {"Status": "invalid"})
    assert hass.states.get(CALL_STATUS).state == "connected"


@pytest.mark.usefixtures("init_integration")
async def test_call_status_refresh_error(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    xget_responses: dict[tuple[str, ...], Any],
) -> None:
    """Test the call status keeps its data when refreshing fails."""
    xget_responses[tuple(CALL_PATH)] = xows.CommandError("Failed")

    await send_feedback(hass, mock_webex_client, CALL_PATH, {})

    assert hass.states.get(CALL_STATUS).state == "idle"


@pytest.mark.usefixtures("init_integration")
async def test_call_status_unexpected_call_data(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    xget_responses: dict[tuple[str, ...], Any],
) -> None:
    """Test unexpected call data is treated as no call."""
    xget_responses[tuple(CALL_PATH)] = "invalid"

    await send_feedback(hass, mock_webex_client, CALL_PATH, {})

    assert hass.states.get(CALL_STATUS).state == "idle"


@pytest.mark.parametrize(
    ("entity_id", "path", "params", "state"),
    [
        (
            AMBIENT_NOISE,
            ["Status", "RoomAnalytics", "AmbientNoise"],
            {"Status": {"RoomAnalytics": {"AmbientNoise": {"Level": {"A": "35"}}}}},
            "35",
        ),
        (
            SOUND_LEVEL,
            ["Status", "RoomAnalytics", "Sound"],
            {"Status": {"RoomAnalytics": {"Sound": {"Level": {"A": "42"}}}}},
            "42",
        ),
        (
            TEMPERATURE,
            ["Status", "RoomAnalytics", "AmbientTemperature"],
            {"Status": {"RoomAnalytics": {"AmbientTemperature": "22.5"}}},
            "22.5",
        ),
        (
            HUMIDITY,
            ["Status", "RoomAnalytics", "RelativeHumidity"],
            {"Status": {"RoomAnalytics": {"RelativeHumidity": "45"}}},
            "45",
        ),
        (
            SYSTEM_UPTIME,
            ["Status", "SystemUnit", "Uptime"],
            {"Status": {"SystemUnit": {"Uptime": "3600"}}},
            "3600",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_measurement_feedback(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    entity_id: str,
    path: list[str],
    params: dict[str, Any],
    state: str,
) -> None:
    """Test measurement sensors follow device feedback."""
    await send_feedback(hass, mock_webex_client, path, params)
    assert hass.states.get(entity_id).state == state

    # Unexpected feedback keeps the last known value
    await send_feedback(hass, mock_webex_client, path, {"Status": "invalid"})
    assert hass.states.get(entity_id).state == state


@pytest.mark.parametrize(
    ("entity_id", "path", "params"),
    [
        (
            AMBIENT_NOISE,
            ["Status", "RoomAnalytics", "AmbientNoise"],
            {"Status": {"RoomAnalytics": {"AmbientNoise": {"Level": {"A": "loud"}}}}},
        ),
        (
            SOUND_LEVEL,
            ["Status", "RoomAnalytics", "Sound"],
            {"Status": {"RoomAnalytics": {"Sound": {"Level": {}}}}},
        ),
        (
            TEMPERATURE,
            ["Status", "RoomAnalytics", "AmbientTemperature"],
            {"Status": {"RoomAnalytics": {"AmbientTemperature": "warm"}}},
        ),
        (
            HUMIDITY,
            ["Status", "RoomAnalytics", "RelativeHumidity"],
            {"Status": {"RoomAnalytics": {}}},
        ),
        (
            SYSTEM_UPTIME,
            ["Status", "SystemUnit", "Uptime"],
            {"Status": {"SystemUnit": {"Uptime": "long"}}},
        ),
        # Feedback without a value
        (
            AMBIENT_NOISE,
            ["Status", "RoomAnalytics", "AmbientNoise"],
            {"Status": {"RoomAnalytics": {"AmbientNoise": {}}}},
        ),
        (
            TEMPERATURE,
            ["Status", "RoomAnalytics", "AmbientTemperature"],
            {"Status": {"RoomAnalytics": {}}},
        ),
        (
            SYSTEM_UPTIME,
            ["Status", "SystemUnit", "Uptime"],
            {"Status": {"SystemUnit": {}}},
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_measurement_feedback_invalid(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    entity_id: str,
    path: list[str],
    params: dict[str, Any],
) -> None:
    """Test invalid measurement feedback is ignored."""
    await send_feedback(hass, mock_webex_client, path, params)
    assert hass.states.get(entity_id).state == STATE_UNKNOWN


@pytest.mark.usefixtures("init_integration")
async def test_room_occupancy(
    hass: HomeAssistant, mock_webex_client: MagicMock
) -> None:
    """Test the room occupancy sensor combines presence details."""
    await send_feedback(
        hass,
        mock_webex_client,
        ["Status", "RoomAnalytics", "PeoplePresence"],
        {"Status": {"RoomAnalytics": {"PeoplePresence": "Yes"}}},
    )
    await send_feedback(
        hass,
        mock_webex_client,
        ["Status", "RoomAnalytics", "PeopleCount"],
        {
            "Status": {
                "RoomAnalytics": {"PeopleCount": {"Current": "3", "Capacity": "8"}}
            }
        },
    )
    await send_feedback(
        hass,
        mock_webex_client,
        ["Status", "Bookings", "Availability", "Status"],
        {"Status": {"Bookings": {"Availability": {"Status": "BUSY"}}}},
    )
    await send_feedback(
        hass,
        mock_webex_client,
        ["Status", "RoomAnalytics", "Engagement", "CloseProximity"],
        {"Status": {"RoomAnalytics": {"Engagement": {"CloseProximity": "True"}}}},
    )

    state = hass.states.get(ROOM_OCCUPANCY)
    assert state.state == "yes"
    assert state.attributes["people_count"] == 3
    assert state.attributes["capacity"] == 8
    assert state.attributes["availability"] == "busy"
    assert state.attributes["close_proximity"] is True


@pytest.mark.parametrize(
    ("path", "params"),
    [
        (
            ["Status", "RoomAnalytics", "PeoplePresence"],
            {"Status": {"RoomAnalytics": {"PeoplePresence": 1}}},
        ),
        (
            ["Status", "RoomAnalytics", "PeoplePresence"],
            {"Status": {"RoomAnalytics": {"PeoplePresence": "Unknown"}}},
        ),
        (
            ["Status", "RoomAnalytics", "PeoplePresence"],
            {"Status": {"RoomAnalytics": {}}},
        ),
        (
            ["Status", "RoomAnalytics", "PeopleCount"],
            {"Status": {"RoomAnalytics": {"PeopleCount": {"Current": "many"}}}},
        ),
        (
            ["Status", "Bookings", "Availability", "Status"],
            {"Status": {"Bookings": {"Availability": {"Status": 1}}}},
        ),
        (
            ["Status", "RoomAnalytics", "Engagement", "CloseProximity"],
            {"Status": "invalid"},
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_room_occupancy_invalid(
    hass: HomeAssistant,
    mock_webex_client: MagicMock,
    path: list[str],
    params: dict[str, Any],
) -> None:
    """Test unexpected room occupancy feedback is ignored."""
    await send_feedback(hass, mock_webex_client, path, params)

    state = hass.states.get(ROOM_OCCUPANCY)
    assert state.state == STATE_UNKNOWN
    assert state.attributes.get("people_count") is None


@pytest.mark.parametrize(
    "bookings",
    [
        [
            {
                "Id": "1",
                "Title": "Earlier meeting",
                "Time": {
                    "StartTime": "2025-01-27T09:00:00Z",
                    "EndTime": "2025-01-27T09:30:00Z",
                },
            },
            {"Id": "2", "Title": "No time"},
            {
                "Id": "3",
                "Title": "Team Standup",
                "Organizer": {"FirstName": "Jane", "Email": "jane@example.com"},
                "MeetingPlatform": "Webex",
                "Privacy": "Public",
                "Time": {
                    "StartTime": "2025-01-27T10:00:00Z",
                    "EndTime": "2025-01-27T10:30:00Z",
                },
            },
            {
                "Id": "5",
                "Title": "Planning",
                "Time": {
                    "StartTime": "2025-01-27T14:00:00Z",
                    "EndTime": "2025-01-27T15:00:00Z",
                },
            },
            {
                "Id": "4",
                "Title": "Review",
                "Organizer": {"FirstName": "John", "Email": "john@example.com"},
                "Time": {"StartTime": "2025-01-27T11:00:00Z"},
            },
        ]
    ],
)
@pytest.mark.freeze_time(NOW)
@pytest.mark.usefixtures("init_integration")
async def test_meetings(hass: HomeAssistant) -> None:
    """Test the current and next meeting sensors."""
    state = hass.states.get(CURRENT_MEETING)
    assert state.state == "Team Standup"
    assert state.attributes["organizer_name"] == "Jane"
    assert state.attributes["organizer_email"] == "jane@example.com"
    assert state.attributes["start_time"] == "2025-01-27T10:00:00+00:00"
    assert state.attributes["end_time"] == "2025-01-27T10:30:00+00:00"
    assert state.attributes["meeting_id"] == "3"
    assert state.attributes["meeting_platform"] == "Webex"
    assert state.attributes["privacy"] == "Public"

    state = hass.states.get(NEXT_MEETING)
    assert state.state == "Review"
    assert state.attributes["organizer_name"] == "John"
    assert state.attributes["start_time"] == "2025-01-27T11:00:00+00:00"
    assert state.attributes["end_time"] is None
    assert state.attributes["meeting_id"] == "4"


@pytest.mark.parametrize(
    "bookings",
    [
        {
            "Id": "1",
            "Title": "Planning",
            "Time": {
                "StartTime": "2025-01-27T14:00:00Z",
                "EndTime": "2025-01-27T15:00:00Z",
            },
        }
    ],
)
@pytest.mark.freeze_time(NOW)
@pytest.mark.usefixtures("init_integration")
async def test_meetings_single_booking(hass: HomeAssistant) -> None:
    """Test a single booking that is not wrapped in a list."""
    assert hass.states.get(CURRENT_MEETING).state == STATE_UNKNOWN

    state = hass.states.get(NEXT_MEETING)
    assert state.state == "Planning"
    assert state.attributes["end_time"] == "2025-01-27T15:00:00+00:00"


@pytest.mark.freeze_time(NOW)
async def test_meetings_update_on_feedback(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_webex_client: MagicMock,
    bookings: list[dict[str, Any]],
) -> None:
    """Test the meeting sensors refresh when the bookings change."""
    assert hass.states.get(CURRENT_MEETING).state == STATE_UNKNOWN
    assert hass.states.get(NEXT_MEETING).state == STATE_UNKNOWN

    bookings.append(
        {
            "Id": "1",
            "Title": "Team Standup",
            "Time": {
                "StartTime": "2025-01-27T10:00:00Z",
                "EndTime": "2025-01-27T10:30:00Z",
            },
        }
    )
    await send_feedback(hass, mock_webex_client, BOOKINGS_PATH, {})

    assert hass.states.get(CURRENT_MEETING).state == "Team Standup"
    assert hass.states.get(NEXT_MEETING).state == STATE_UNKNOWN


@pytest.mark.parametrize(
    "bookings",
    [[{"Id": "1", "Title": "Broken", "Time": {"StartTime": "tomorrow"}}]],
)
@pytest.mark.usefixtures("init_integration")
async def test_meetings_invalid_bookings(hass: HomeAssistant) -> None:
    """Test bookings that cannot be parsed are ignored."""
    assert hass.states.get(CURRENT_MEETING).state == STATE_UNKNOWN
    assert hass.states.get(NEXT_MEETING).state == STATE_UNKNOWN


@pytest.mark.usefixtures("init_integration")
async def test_meetings_update_error(
    hass: HomeAssistant, mock_webex_client: MagicMock
) -> None:
    """Test the meeting sensors keep their state when bookings cannot be read."""
    mock_webex_client.xcommand.side_effect = xows.CommandError("Failed")

    await send_feedback(hass, mock_webex_client, BOOKINGS_PATH, {})

    assert hass.states.get(CURRENT_MEETING).state == STATE_UNKNOWN
    assert hass.states.get(NEXT_MEETING).state == STATE_UNKNOWN


@pytest.mark.parametrize(
    "xget_responses",
    [
        {
            ("Status", "Network"): [
                {
                    "Ethernet": {
                        "MacAddress": "00:11:22:33:44:55",
                        "Speed": "1000full",
                    },
                    "IPv4": {"Address": "192.168.1.100", "Gateway": "192.168.1.1"},
                    "IPv6": {"LinkLocalAddress": "fe80::1"},
                    "VLAN": {"Voice": {"VlanId": "100"}},
                }
            ]
        }
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_network_status(
    hass: HomeAssistant, mock_webex_client: MagicMock
) -> None:
    """Test the network status sensor reads the initial network state."""
    state = hass.states.get(NETWORK_STATUS)
    assert state.state == "connected"
    assert state.attributes["ipv4_address"] == "192.168.1.100"
    assert state.attributes["gateway"] == "192.168.1.1"
    assert state.attributes["ipv6_address"] == "fe80::1"
    assert state.attributes["mac_address"] == "00:11:22:33:44:55"
    assert state.attributes["speed_mbps"] == 1000
    assert state.attributes["vlan_id"] == 100

    await send_feedback(
        hass,
        mock_webex_client,
        NETWORK_PATH,
        {"Status": {"Network": [{"IPv4": {}, "VLAN": {"Voice": {"VlanId": "Off"}}}]}},
    )

    state = hass.states.get(NETWORK_STATUS)
    assert state.state == "disconnected"
    assert state.attributes.get("ipv4_address") is None
    assert state.attributes.get("vlan_id") is None


@pytest.mark.parametrize(
    "xget_responses", [{("Status", "Network"): xows.CommandError("Failed")}]
)
@pytest.mark.usefixtures("init_integration")
async def test_network_status_invalid(
    hass: HomeAssistant, mock_webex_client: MagicMock
) -> None:
    """Test the network status handles unreadable network data."""
    assert hass.states.get(NETWORK_STATUS).state == STATE_UNKNOWN

    await send_feedback(
        hass, mock_webex_client, NETWORK_PATH, {"Status": {"Network": ["invalid"]}}
    )
    assert hass.states.get(NETWORK_STATUS).state == STATE_UNKNOWN


@pytest.mark.parametrize(
    "entity_id",
    [
        CALL_STATUS,
        CURRENT_MEETING,
        NEXT_MEETING,
        ROOM_OCCUPANCY,
        SOUND_LEVEL,
        AMBIENT_NOISE,
        TEMPERATURE,
        HUMIDITY,
        NETWORK_STATUS,
        SYSTEM_UPTIME,
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_availability(
    hass: HomeAssistant, mock_webex_client: MagicMock, entity_id: str
) -> None:
    """Test sensors follow the connection state of the device."""
    await set_connected(hass, mock_webex_client, False)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    await set_connected(hass, mock_webex_client, True)
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE
