"""Select platform for Webex CE devices."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import WebexCEEntity
from .models import WebexCEConfigEntry, WebexCEData

_LOGGER = logging.getLogger(__name__)

# Limit parallel updates to avoid overwhelming device
PARALLEL_UPDATES = 1

# Map xAPI standby states to options
STANDBY_STATES = {
    "Off": "awake",
    "Halfwake": "halfwake",
    "Standby": "sleep",
}

# Map options to the xAPI command that selects them
STANDBY_COMMANDS = {
    "awake": ["Standby", "Deactivate"],
    "halfwake": ["Standby", "Halfwake"],
    "sleep": ["Standby", "Activate"],
}

STANDBY_ICONS = {
    "awake": "mdi:monitor",
    "halfwake": "mdi:power-sleep",
    "sleep": "mdi:power",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WebexCEConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Webex CE select entities."""
    data = entry.runtime_data
    async_add_entities(
        [
            WebexCECameraPresetSelect(data),
            WebexCEPresentationSourceSelect(data),
            WebexCEStandbySelect(data),
        ]
    )


class WebexCEStandbySelect(WebexCEEntity, SelectEntity):
    """Representation of a Webex CE standby state select."""

    _attr_translation_key = "standby"
    _attr_options = list(STANDBY_COMMANDS)
    _attr_icon = "mdi:monitor"

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the select."""
        super().__init__(data, "standby")

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(["Status", "Standby"], self._handle_feedback)

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle feedback from the device."""
        _LOGGER.debug("Received standby feedback for %s: %s", self.unique_id, params)

        try:
            standby_data = params.get("Status", {}).get("Standby")
        except AttributeError:
            _LOGGER.warning("Unexpected standby feedback format: %s", params)
            return

        # The state is either reported directly or wrapped in a dict
        if isinstance(standby_data, dict):
            standby_data = standby_data.get("State")

        option = (
            STANDBY_STATES.get(standby_data) if isinstance(standby_data, str) else None
        )
        if option is None:
            _LOGGER.warning("Unknown standby state: %s", standby_data)
            return

        self._set_option(option)

    @callback
    def _set_option(self, option: str) -> None:
        """Update the state for a standby option."""
        self._attr_current_option = option
        self._attr_icon = STANDBY_ICONS[option]
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Change the standby state."""
        await self._async_command(STANDBY_COMMANDS[option])
        # Optimistically update the state, feedback confirms it later
        self._set_option(option)


class WebexCEPresentationSourceSelect(WebexCEEntity, SelectEntity):
    """Select the video input connector to present.

    The current option is the source of the local presentation, or unknown
    when nothing is presented locally.
    """

    _attr_translation_key = "presentation_source"
    _attr_options = ["1", "2", "3", "4", "5"]
    _attr_icon = "mdi:video-input-hdmi"

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the select."""
        super().__init__(data, "presentation_source")
        # Source of each local presentation instance, by instance ID
        self._sources: dict[str, str] = {}

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(
            ["Status", "Conference", "Presentation", "LocalInstance"],
            self._handle_feedback,
        )

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle feedback from the device."""
        try:
            instances = (
                params.get("Status", {})
                .get("Conference", {})
                .get("Presentation", {})
                .get("LocalInstance")
            )
        except AttributeError:
            _LOGGER.warning("Unexpected presentation source feedback: %s", params)
            return

        if not isinstance(instances, list):
            return

        for instance in instances:
            if not isinstance(instance, dict) or "id" not in instance:
                continue
            instance_id = str(instance["id"])
            # A stopped presentation is reported as a "ghost" instance
            if instance.get("ghost") in ("True", True):
                self._sources.pop(instance_id, None)
            elif (source := instance.get("Source")) is not None:
                self._sources[instance_id] = str(source)

        self._attr_current_option = next(
            (
                source
                for source in self._sources.values()
                if source in self._attr_options
            ),
            None,
        )
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Present the selected video input connector."""
        await self._async_command(["Presentation", "Start"], ConnectorId=int(option))
        # Optimistically update the state, feedback confirms it later
        self._attr_current_option = option
        self.async_write_ha_state()


class WebexCECameraPresetSelect(WebexCEEntity, SelectEntity):
    """Representation of camera preset select."""

    _attr_translation_key = "camera_preset"
    _attr_options = [str(i) for i in range(1, 36)]  # Presets 1-35
    _attr_icon = "mdi:camera-control"

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the select."""
        super().__init__(data, "camera_preset")

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(
            ["Status", "Cameras", "Camera"], self._handle_feedback
        )

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle feedback from the device."""
        try:
            camera_data = params.get("Status", {}).get("Cameras", {}).get("Camera")
        except AttributeError:
            _LOGGER.warning("Unexpected camera preset feedback: %s", params)
            return

        if not isinstance(camera_data, list):
            return
        for camera in camera_data:
            if not isinstance(camera, dict):
                continue
            position = camera.get("Position", {})
            if not isinstance(position, dict):
                continue
            preset = position.get("ActivePreset")
            if preset and str(preset) in self._attr_options:
                self._attr_current_option = str(preset)
                self.async_write_ha_state()
                return

    async def async_select_option(self, option: str) -> None:
        """Activate the camera preset."""
        await self._async_command(
            ["Camera", "Preset", "Activate"], PresetId=int(option)
        )
        # Optimistically update the state, feedback confirms it later
        self._attr_current_option = option
        self.async_write_ha_state()
