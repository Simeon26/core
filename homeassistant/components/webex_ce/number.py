"""Number platform for Webex CE devices."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import WebexCEEntity
from .models import WebexCEConfigEntry, WebexCEData

_LOGGER = logging.getLogger(__name__)

# Limit parallel updates to avoid overwhelming device
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WebexCEConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Webex CE number entities."""
    async_add_entities([WebexCEVolumeNumber(entry.runtime_data)])


def _volume_icon(volume: int) -> str:
    """Return the icon for a volume level."""
    if volume == 0:
        return "mdi:volume-off"
    if volume < 33:
        return "mdi:volume-low"
    if volume < 67:
        return "mdi:volume-medium"
    return "mdi:volume-high"


class WebexCEVolumeNumber(WebexCEEntity, NumberEntity):
    """Representation of a Webex CE volume control."""

    _attr_translation_key = "volume"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:volume-high"

    def __init__(self, data: WebexCEData) -> None:
        """Initialize the number entity."""
        super().__init__(data, "volume")

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(
            ["Status", "Audio", "Volume"], self._handle_feedback
        )

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle feedback from the device."""
        _LOGGER.debug("Received volume feedback for %s: %s", self.unique_id, params)

        try:
            volume_data = params.get("Status", {}).get("Audio", {}).get("Volume")
            # The volume is either reported directly or wrapped in a dict
            if isinstance(volume_data, dict):
                volume_data = next(
                    (
                        volume_data[key]
                        for key in ("value", "Value")
                        if volume_data.get(key) is not None
                    ),
                    None,
                )
            if volume_data is None:
                return
            volume = int(volume_data)
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.warning("Unexpected volume feedback format: %s - %s", params, err)
            return

        self._set_volume(volume)

    @callback
    def _set_volume(self, volume: int) -> None:
        """Update the state for a volume level."""
        self._attr_native_value = volume
        self._attr_icon = _volume_icon(volume)
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Set the volume level."""
        volume = int(value)
        await self._async_command(["Audio", "Volume", "Set"], Level=volume)
        # Optimistically update the state, feedback confirms it later
        self._set_volume(volume)
