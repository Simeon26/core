"""Sensor platform for Webex CE devices."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
import re
from typing import Any

import xows

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
import homeassistant.util.dt as dt_util

from .entity import WebexCEEntity
from .models import WebexCEConfigEntry, WebexCEData

_LOGGER = logging.getLogger(__name__)

# Limit parallel updates to avoid overwhelming device
PARALLEL_UPDATES = 1

# Map xAPI call fields to call status sensor attribute names
CALL_ATTRIBUTES = {
    "DisplayName": "display_name",
    "RemoteNumber": "remote_number",
    "CallbackNumber": "callback_number",
    "Protocol": "protocol",
    "CallType": "call_type",
    "CallId": "call_id",
}

CALL_STATE_IDLE = "idle"
CALL_STATE_CONNECTING = "connecting"
CALL_STATE_RINGING = "ringing"
CALL_STATE_CONNECTED = "connected"
CALL_STATE_ON_HOLD = "on_hold"
CALL_STATE_DISCONNECTING = "disconnecting"

# Map lowercased xAPI call status values to call status sensor states
CALL_STATES = {
    "idle": CALL_STATE_IDLE,
    "dialling": CALL_STATE_CONNECTING,
    "dialing": CALL_STATE_CONNECTING,
    "connecting": CALL_STATE_CONNECTING,
    "earlymedia": CALL_STATE_CONNECTING,
    "ringing": CALL_STATE_RINGING,
    "alerting": CALL_STATE_RINGING,
    "connected": CALL_STATE_CONNECTED,
    "onhold": CALL_STATE_ON_HOLD,
    "disconnecting": CALL_STATE_DISCONNECTING,
    "ondisconnect": CALL_STATE_DISCONNECTING,
}

CALL_ICONS = {
    CALL_STATE_CONNECTED: "mdi:phone-in-talk",
    CALL_STATE_RINGING: "mdi:phone-ring",
    CALL_STATE_ON_HOLD: "mdi:phone-paused",
    CALL_STATE_CONNECTING: "mdi:phone-clock",
    CALL_STATE_DISCONNECTING: "mdi:phone-clock",
}

# Call states during which the call duration is reported
CALL_STATES_WITH_DURATION = (CALL_STATE_CONNECTED, CALL_STATE_ON_HOLD)

# Map xAPI people presence values to room occupancy states
PRESENCE_STATES = {"yes": "yes", "no": "no"}

BOOKING_ATTRIBUTES = (
    "organizer_name",
    "organizer_email",
    "start_time",
    "end_time",
    "meeting_id",
    "meeting_platform",
    "privacy",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WebexCEConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Webex CE sensor entities."""
    data = entry.runtime_data
    async_add_entities(
        [
            # Call and meeting status
            WebexCECallStatusSensor(data),
            WebexCECurrentMeetingSensor(data),
            WebexCENextMeetingSensor(data),
            # Room analytics
            WebexCEPeoplePresenceSensor(data),
            WebexCESoundLevelSensor(data),
            WebexCEAmbientNoiseSensor(data),
            WebexCETemperatureSensor(data),
            WebexCEHumiditySensor(data),
            # Network and system
            WebexCENetworkStatusSensor(data),
            WebexCESystemUptimeSensor(data),
        ]
    )


class WebexCESensor(WebexCEEntity, SensorEntity):
    """Base class for Webex CE sensors."""


class WebexCECallStatusSensor(WebexCESensor):
    """Sensor for active call status and details.

    Monitors Status/Call and Status/Conference xAPI paths to provide
    current call state (idle/connected/ringing etc), call direction,
    duration, and remote party information.
    """

    _attr_translation_key = "call_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        CALL_STATE_IDLE,
        CALL_STATE_CONNECTING,
        CALL_STATE_RINGING,
        CALL_STATE_CONNECTED,
        CALL_STATE_ON_HOLD,
        CALL_STATE_DISCONNECTING,
    ]

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the sensor."""
        super().__init__(data, "call_status")
        self._attr_native_value = CALL_STATE_IDLE
        self._attr_extra_state_attributes = {}
        self._call_data: dict[str, Any] | None = None
        self._conference_data: dict[str, Any] | None = None
        self._call_start_time: datetime | None = None

    @property
    def icon(self) -> str:
        """Return the icon based on call state."""
        return CALL_ICONS.get(str(self._attr_native_value), "mdi:phone-off")

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(
            ["Status", "Call"], self._handle_call_feedback, "call"
        )
        await self._async_subscribe(
            ["Status", "Conference"], self._handle_conference_feedback, "conference"
        )

    @callback
    def _handle_call_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle call feedback from the device.

        Feedback only contains the values that changed, so read the full call
        status from the device.
        """
        _LOGGER.debug("Received call feedback for %s: %s", self.unique_id, params)
        self.hass.async_create_task(self._refresh_call_status())

    @callback
    def _handle_conference_feedback(
        self, params: dict[str, Any], feedback_id: Any
    ) -> None:
        """Handle conference feedback from the device."""
        _LOGGER.debug("Received conference feedback for %s: %s", self.unique_id, params)
        try:
            conference_data = params.get("Status", {}).get("Conference")
        except AttributeError:
            _LOGGER.warning("Unexpected conference feedback format: %s", params)
            return
        self._conference_data = conference_data
        self._update_state()

    async def _refresh_call_status(self) -> None:
        """Refresh call status by querying the device."""
        try:
            call_status = await self._client.xget(["Status", "Call"])
            # The call status is a list of calls, the first one is reported
            if isinstance(call_status, list):
                call_status = call_status[0] if call_status else None
            self._call_data = call_status if isinstance(call_status, dict) else None

            # Without a call, a meeting may still be active
            if self._call_data is None:
                self._conference_data = await self._client.xget(
                    ["Status", "Conference"]
                )
        except (xows.XoWSError, OSError) as err:
            _LOGGER.warning("Could not refresh call status: %s", err)

        self._update_state()

    def _extract_call_attributes(self, call_data: dict[str, Any]) -> dict[str, Any]:
        """Extract attributes from call data."""
        attributes: dict[str, Any] = {
            attr_key: call_data[key]
            for key, attr_key in CALL_ATTRIBUTES.items()
            if key in call_data
        }
        if "Direction" in call_data:
            attributes["direction"] = str(call_data["Direction"]).lower()
        return attributes

    def _extract_conference_attributes(self) -> dict[str, Any]:
        """Extract attributes from conference data."""
        attributes: dict[str, Any] = {}
        if not isinstance(self._conference_data, dict):
            return attributes

        pres_data = self._conference_data.get("Presentation", {})
        if isinstance(pres_data, dict) and "Mode" in pres_data:
            attributes["presentation_mode"] = pres_data["Mode"]

        meeting_data = self._conference_data.get("ActiveMeeting", {})
        if isinstance(meeting_data, dict):
            if "Name" in meeting_data:
                attributes["meeting_name"] = meeting_data["Name"]
            if "Id" in meeting_data:
                attributes["meeting_id"] = meeting_data["Id"]

        participants = self._conference_data.get("Participants", {})
        if isinstance(participants, dict) and "Count" in participants:
            attributes["participants_count"] = participants["Count"]

        return attributes

    def _has_active_conference(self) -> bool:
        """Return if the conference status reports an active meeting or call."""
        if not isinstance(self._conference_data, dict):
            return False
        # An active meeting has at least a name or an ID
        active_meeting = self._conference_data.get("ActiveMeeting")
        if isinstance(active_meeting, dict) and (
            active_meeting.get("Name") or active_meeting.get("Id")
        ):
            return True
        call_info = self._conference_data.get("Call")
        return isinstance(call_info, (list, dict)) and bool(call_info)

    @staticmethod
    def _call_state(call_data: dict[str, Any]) -> str | None:
        """Return the sensor state for the data of a call."""
        if status := str(call_data.get("Status", "")).lower():
            # The status is leading: a held call is still answered
            if (state := CALL_STATES.get(status)) is None:
                _LOGGER.debug("Unknown call status: %s", call_data["Status"])
            return state
        # The status may be missing once the call is established
        if call_data.get("AnswerState") == "Answered":
            return CALL_STATE_CONNECTED
        return CALL_STATE_IDLE

    @callback
    def _update_state(self) -> None:
        """Update the sensor state and attributes from call and conference data."""
        attributes: dict[str, Any] = {}

        if self._call_data:
            self._attr_native_value = self._call_state(self._call_data)
            attributes.update(self._extract_call_attributes(self._call_data))

            # Time the call from the moment it is answered
            answer_state = self._call_data.get("AnswerState")
            if answer_state == "Answered" and not self._call_start_time:
                self._call_start_time = dt_util.utcnow()
            elif answer_state and answer_state != "Answered":
                self._call_start_time = None
        elif self._has_active_conference():
            # A meeting can be active while the call status is empty
            self._attr_native_value = CALL_STATE_CONNECTED
            if not self._call_start_time:
                self._call_start_time = dt_util.utcnow()
        else:
            self._attr_native_value = CALL_STATE_IDLE
            self._call_start_time = None

        if (
            self._call_start_time
            and self._attr_native_value in CALL_STATES_WITH_DURATION
        ):
            duration = dt_util.utcnow() - self._call_start_time
            attributes["duration"] = str(duration).split(".", maxsplit=1)[0]
            attributes["duration_seconds"] = int(duration.total_seconds())

        attributes.update(self._extract_conference_attributes())
        self._attr_extra_state_attributes = attributes
        self.async_write_ha_state()


class WebexCEAmbientNoiseSensor(WebexCESensor):
    """Representation of ambient noise level sensor."""

    _attr_translation_key = "ambient_noise"
    _attr_native_unit_of_measurement = "dBA"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:volume-high"

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the sensor."""
        super().__init__(data, "ambient_noise")

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(
            ["Status", "RoomAnalytics", "AmbientNoise"], self._handle_feedback
        )

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle feedback from the device."""
        try:
            noise_data = (
                params.get("Status", {})
                .get("RoomAnalytics", {})
                .get("AmbientNoise", {})
            )
            level_a = noise_data.get("Level", {}).get("A")
            if level_a is None:
                return
            value = int(level_a)
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Unexpected ambient noise feedback: %s, error: %s", params, err
            )
            return
        self._attr_native_value = value
        self.async_write_ha_state()


class WebexCETemperatureSensor(WebexCESensor):
    """Representation of ambient temperature sensor."""

    _attr_translation_key = "ambient_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the sensor."""
        super().__init__(data, "ambient_temperature")

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(
            ["Status", "RoomAnalytics", "AmbientTemperature"], self._handle_feedback
        )

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle feedback from the device."""
        try:
            temp = (
                params.get("Status", {})
                .get("RoomAnalytics", {})
                .get("AmbientTemperature")
            )
            if temp is None:
                return
            value = float(temp)
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Unexpected temperature feedback: %s, error: %s", params, err
            )
            return
        self._attr_native_value = value
        self.async_write_ha_state()


class WebexCEPeoplePresenceSensor(WebexCESensor):
    """Representation of room occupancy sensor with presence details."""

    _attr_translation_key = "room_occupancy"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(PRESENCE_STATES.values())
    _attr_icon = "mdi:account-multiple"

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the sensor."""
        super().__init__(data, "room_occupancy")
        self._attr_extra_state_attributes = {
            "people_count": None,
            "capacity": None,
            "availability": None,
            "close_proximity": None,
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(
            ["Status", "RoomAnalytics", "PeoplePresence"],
            self._handle_presence_feedback,
            "presence",
        )
        await self._async_subscribe(
            ["Status", "RoomAnalytics", "PeopleCount"],
            self._handle_count_feedback,
            "count",
        )
        await self._async_subscribe(
            ["Status", "Bookings", "Availability", "Status"],
            self._handle_availability_feedback,
            "availability",
        )
        await self._async_subscribe(
            ["Status", "RoomAnalytics", "Engagement", "CloseProximity"],
            self._handle_proximity_feedback,
            "proximity",
        )

    @callback
    def _handle_presence_feedback(
        self, params: dict[str, Any], feedback_id: Any
    ) -> None:
        """Handle presence feedback from the device."""
        try:
            presence = (
                params.get("Status", {}).get("RoomAnalytics", {}).get("PeoplePresence")
            )
            if presence is None:
                return
            # The device reports "Unknown" when presence detection is off
            state = PRESENCE_STATES.get(presence.lower())
        except AttributeError as err:
            _LOGGER.debug(
                "Unexpected people presence feedback: %s, error: %s", params, err
            )
            return
        self._attr_native_value = state
        self.async_write_ha_state()

    @callback
    def _handle_count_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle count feedback from the device."""
        try:
            people_data = (
                params.get("Status", {}).get("RoomAnalytics", {}).get("PeopleCount", {})
            )
            updates = {
                attribute: int(value)
                for attribute, key in (
                    ("people_count", "Current"),
                    ("capacity", "Capacity"),
                )
                if (value := people_data.get(key)) is not None
            }
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.debug(
                "Unexpected people count feedback: %s, error: %s", params, err
            )
            return
        self._attr_extra_state_attributes.update(updates)
        self.async_write_ha_state()

    @callback
    def _handle_availability_feedback(
        self, params: dict[str, Any], feedback_id: Any
    ) -> None:
        """Handle room availability feedback from the device."""
        try:
            status = (
                params.get("Status", {})
                .get("Bookings", {})
                .get("Availability", {})
                .get("Status", "Free")
            )
            availability = status.lower()
        except AttributeError as err:
            _LOGGER.debug(
                "Unexpected availability feedback: %s, error: %s", params, err
            )
            return
        self._attr_extra_state_attributes["availability"] = availability
        self.async_write_ha_state()

    @callback
    def _handle_proximity_feedback(
        self, params: dict[str, Any], feedback_id: Any
    ) -> None:
        """Handle close proximity feedback from the device."""
        try:
            proximity = (
                params.get("Status", {})
                .get("RoomAnalytics", {})
                .get("Engagement", {})
                .get("CloseProximity", "False")
            )
        except AttributeError as err:
            _LOGGER.debug("Unexpected proximity feedback: %s, error: %s", params, err)
            return
        self._attr_extra_state_attributes["close_proximity"] = proximity in (
            "True",
            True,
            "true",
        )
        self.async_write_ha_state()


class WebexCEHumiditySensor(WebexCESensor):
    """Representation of relative humidity sensor."""

    _attr_translation_key = "relative_humidity"
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the sensor."""
        super().__init__(data, "relative_humidity")

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(
            ["Status", "RoomAnalytics", "RelativeHumidity"], self._handle_feedback
        )

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle feedback from the device."""
        try:
            humidity = (
                params.get("Status", {})
                .get("RoomAnalytics", {})
                .get("RelativeHumidity")
            )
            if humidity is None:
                return
            value = int(humidity)
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.warning("Unexpected humidity feedback: %s, error: %s", params, err)
            return
        self._attr_native_value = value
        self.async_write_ha_state()


class WebexCESoundLevelSensor(WebexCESensor):
    """Representation of sound level sensor."""

    _attr_translation_key = "sound_level"
    _attr_native_unit_of_measurement = "dBA"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:waveform"

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the sensor."""
        super().__init__(data, "sound_level")

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(
            ["Status", "RoomAnalytics", "Sound"], self._handle_feedback
        )

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle feedback from the device."""
        try:
            sound_data = (
                params.get("Status", {}).get("RoomAnalytics", {}).get("Sound", {})
            )
            level_a = sound_data.get("Level", {}).get("A")
            if level_a is None:
                return
            value = int(level_a)
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Unexpected sound level feedback: %s, error: %s", params, err
            )
            return
        self._attr_native_value = value
        self.async_write_ha_state()


type Booking = tuple[datetime, datetime | None, dict[str, Any]]


def _current_booking(bookings: list[Booking], now: datetime) -> Booking | None:
    """Return the booking that is in progress."""
    return next(
        (
            booking
            for booking in bookings
            if booking[1] is not None and booking[0] <= now < booking[1]
        ),
        None,
    )


def _next_booking(bookings: list[Booking], now: datetime) -> Booking | None:
    """Return the earliest booking that has not started yet."""
    upcoming = [booking for booking in bookings if booking[0] > now]
    return min(upcoming, key=lambda booking: booking[0], default=None)


def _parse_bookings(result: Any) -> list[Booking]:
    """Return the start time, end time and details of each booking."""
    bookings = result.get("Booking", [])
    if not isinstance(bookings, list):
        bookings = [bookings] if bookings else []

    parsed: list[Booking] = []
    for booking in bookings:
        time_data = booking.get("Time", {})
        if not (start := time_data.get("StartTime")):
            continue
        end = time_data.get("EndTime")
        parsed.append(
            (
                datetime.fromisoformat(start),
                datetime.fromisoformat(end) if end else None,
                booking,
            )
        )
    return parsed


class WebexCEBookingSensor(WebexCESensor):
    """Base class for sensors that show a booking of the device."""

    def __init__(
        self,
        data: WebexCEData,
        key: str,
        select_booking: Callable[[list[Booking], datetime], Booking | None],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(data, key)
        self._select_booking = select_booking
        self._attr_extra_state_attributes = dict.fromkeys(BOOKING_ATTRIBUTES)

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(["Status", "Bookings"], self._handle_feedback)
        await self._update_bookings()

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle booking status changes."""
        self.hass.async_create_task(self._update_bookings())

    async def _update_bookings(self) -> None:
        """Query the bookings and update the sensor."""
        try:
            result = await self._client.xcommand(["Bookings", "List"])
        except (xows.XoWSError, OSError) as err:
            _LOGGER.debug("Could not read bookings: %s", err)
            return

        try:
            booking = self._select_booking(_parse_bookings(result), dt_util.utcnow())
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.debug("Unexpected bookings: %s, error: %s", result, err)
            return

        if booking is None:
            self._attr_native_value = None
            self._attr_extra_state_attributes = dict.fromkeys(BOOKING_ATTRIBUTES)
        else:
            start, end, details = booking
            organizer = details.get("Organizer", {})
            self._attr_native_value = details.get("Title")
            self._attr_extra_state_attributes = {
                "organizer_name": organizer.get("FirstName", ""),
                "organizer_email": organizer.get("Email", ""),
                "start_time": start.isoformat(),
                "end_time": end.isoformat() if end else None,
                "meeting_id": details.get("Id", ""),
                "meeting_platform": details.get("MeetingPlatform", ""),
                "privacy": details.get("Privacy", ""),
            }
        self.async_write_ha_state()


class WebexCECurrentMeetingSensor(WebexCEBookingSensor):
    """Representation of current meeting sensor with details in attributes."""

    _attr_translation_key = "current_meeting"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the sensor."""
        super().__init__(data, "current_meeting", _current_booking)


class WebexCENextMeetingSensor(WebexCEBookingSensor):
    """Representation of next meeting sensor with details in attributes."""

    _attr_translation_key = "next_meeting"
    _attr_icon = "mdi:calendar-arrow-right"

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the sensor."""
        super().__init__(data, "next_meeting", _next_booking)


class WebexCESystemUptimeSensor(WebexCESensor):
    """Representation of system uptime sensor."""

    _attr_translation_key = "system_uptime"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "s"
    _attr_icon = "mdi:timer"

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the sensor."""
        super().__init__(data, "system_uptime")

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(
            ["Status", "SystemUnit", "Uptime"], self._handle_feedback
        )

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle feedback from the device."""
        try:
            uptime = params.get("Status", {}).get("SystemUnit", {}).get("Uptime")
            if uptime is None:
                return
            value = int(uptime)
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.warning("Unexpected uptime feedback: %s - %s", params, err)
            return
        self._attr_native_value = value
        self.async_write_ha_state()


class WebexCENetworkStatusSensor(WebexCESensor):
    """Representation of network status sensor with detailed attributes."""

    _attr_translation_key = "network_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["connected", "disconnected"]
    _attr_icon = "mdi:lan-connect"

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the sensor."""
        super().__init__(data, "network_status")
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(["Status", "Network"], self._handle_feedback)
        # Query initial state
        try:
            status = await self._client.xget(["Status", "Network"])
        except (xows.XoWSError, OSError) as err:
            _LOGGER.debug("Could not query initial network state: %s", err)
            return
        self._handle_feedback({"Status": {"Network": status}}, None)

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle feedback from the device."""
        try:
            network_list = params.get("Status", {}).get("Network", [])
            if not isinstance(network_list, list) or not network_list:
                return
            state, attributes = self._parse_network(network_list[0])
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.debug("Could not parse network status: %s", err)
            return
        self._attr_native_value = state
        self._attr_extra_state_attributes = attributes
        self.async_write_ha_state()

    @staticmethod
    def _parse_network(network_data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Return the state and attributes for the network status."""
        attributes: dict[str, Any] = {}

        # The device is connected when it has an IPv4 address
        ipv4_data = network_data.get("IPv4", {})
        if ipv4_address := ipv4_data.get("Address"):
            attributes["ipv4_address"] = ipv4_address
            if gateway := ipv4_data.get("Gateway"):
                attributes["gateway"] = gateway

        ipv6_data = network_data.get("IPv6", {})
        if ipv6_address := ipv6_data.get("Address") or ipv6_data.get(
            "LinkLocalAddress"
        ):
            attributes["ipv6_address"] = ipv6_address

        ethernet = network_data.get("Ethernet", {})
        if mac := ethernet.get("MacAddress"):
            attributes["mac_address"] = mac
        if (speed := ethernet.get("Speed")) and (
            match := re.match(r"(\d+)", str(speed))
        ):
            attributes["speed_mbps"] = int(match.group(1))

        vlan_id = network_data.get("VLAN", {}).get("Voice", {}).get("VlanId")
        if vlan_id and vlan_id != "Off":
            attributes["vlan_id"] = int(vlan_id)

        return ("connected" if ipv4_address else "disconnected"), attributes
