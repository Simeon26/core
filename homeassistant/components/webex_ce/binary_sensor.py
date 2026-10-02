"""Binary sensor platform for Webex CE devices."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import WebexCEEntity
from .models import WebexCEConfigEntry, WebexCEData

_LOGGER = logging.getLogger(__name__)

# Entities are updated by device feedback, nothing is polled
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class WebexCEActivityEntityDescription(BinarySensorEntityDescription):
    """Describes a binary sensor for an activity with a duration."""

    area: str
    active_states: tuple[str, ...]


BINARY_SENSORS: tuple[WebexCEActivityEntityDescription, ...] = (
    WebexCEActivityEntityDescription(
        key="recording",
        translation_key="recording",
        icon="mdi:record-rec",
        area="Recording",
        active_states=("Recording", "Active"),
    ),
    WebexCEActivityEntityDescription(
        key="streaming",
        translation_key="streaming",
        icon="mdi:access-point-network",
        area="Streaming",
        active_states=("Streaming", "Active"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WebexCEConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Webex CE binary sensor entities."""
    async_add_entities(
        WebexCEActivityBinarySensor(entry.runtime_data, description)
        for description in BINARY_SENSORS
    )


class WebexCEActivityBinarySensor(WebexCEEntity, BinarySensorEntity):
    """Binary sensor that is on while the device records or streams."""

    entity_description: WebexCEActivityEntityDescription
    _attr_is_on = False

    def __init__(
        self, data: WebexCEData, description: WebexCEActivityEntityDescription
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(data, description.key)
        self.entity_description = description
        self._attr_extra_state_attributes = {"duration": None}

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        area = self.entity_description.area
        await self._async_subscribe(
            ["Status", area, "Status"], self._handle_status_feedback, "status"
        )
        await self._async_subscribe(
            ["Status", area, "Duration"], self._handle_duration_feedback, "duration"
        )

    def _get_value(self, params: dict[str, Any], key: str) -> Any:
        """Return a value of the feedback for the area of this sensor."""
        return params.get("Status", {}).get(self.entity_description.area, {}).get(key)

    @callback
    def _handle_status_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle status feedback from the device."""
        try:
            status = self._get_value(params, "Status")
        except AttributeError:
            _LOGGER.debug(
                "Unexpected %s feedback: %s", self.entity_description.key, params
            )
            return
        self._attr_is_on = status in self.entity_description.active_states
        self.async_write_ha_state()

    @callback
    def _handle_duration_feedback(
        self, params: dict[str, Any], feedback_id: Any
    ) -> None:
        """Handle duration feedback from the device."""
        try:
            duration = self._get_value(params, "Duration")
            duration = int(duration) if duration is not None else 0
        except (AttributeError, TypeError, ValueError):
            _LOGGER.debug(
                "Unexpected %s feedback: %s", self.entity_description.key, params
            )
            return
        self._attr_extra_state_attributes = {"duration": duration}
        self.async_write_ha_state()
