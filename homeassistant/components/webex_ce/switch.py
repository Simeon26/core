"""Switch platform for Webex CE devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import WebexCEEntity
from .models import WebexCEConfigEntry, WebexCEData

_LOGGER = logging.getLogger(__name__)

# Limit parallel updates to avoid overwhelming device
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class WebexCESwitchEntityDescription(SwitchEntityDescription):
    """Describes a Webex CE switch."""

    status_path: list[str]
    is_on_fn: Callable[[Any], bool]
    on_command: tuple[list[str], dict[str, Any]]
    off_command: tuple[list[str], dict[str, Any]]
    icon_off: str | None = None


SWITCHES: tuple[WebexCESwitchEntityDescription, ...] = (
    WebexCESwitchEntityDescription(
        key="microphone_mute",
        translation_key="microphone_mute",
        icon="mdi:microphone-off",
        icon_off="mdi:microphone",
        status_path=["Status", "Audio", "Microphones", "Mute"],
        is_on_fn=lambda value: value == "On",
        on_command=(["Audio", "Microphones", "Mute"], {}),
        off_command=(["Audio", "Microphones", "Unmute"], {}),
    ),
    WebexCESwitchEntityDescription(
        key="video_mute",
        translation_key="video_mute",
        icon="mdi:video-off",
        icon_off="mdi:video",
        status_path=["Status", "Video", "Input", "MainVideoMute"],
        is_on_fn=lambda value: value == "On",
        on_command=(["Video", "Input", "MainVideo", "Mute"], {}),
        off_command=(["Video", "Input", "MainVideo", "Unmute"], {}),
    ),
    WebexCESwitchEntityDescription(
        key="presentation",
        translation_key="presentation",
        icon="mdi:presentation",
        status_path=["Status", "Conference", "Presentation", "Mode"],
        is_on_fn=lambda value: value in ("Sending", "Receiving", "On"),
        on_command=(["Presentation", "Start"], {}),
        off_command=(["Presentation", "Stop"], {}),
    ),
    WebexCESwitchEntityDescription(
        key="self_view",
        translation_key="self_view",
        icon="mdi:monitor-eye",
        status_path=["Status", "Video", "Selfview", "Mode"],
        is_on_fn=lambda value: value == "On",
        on_command=(["Video", "Selfview", "Set"], {"Mode": "On"}),
        off_command=(["Video", "Selfview", "Set"], {"Mode": "Off"}),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WebexCEConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Webex CE switch entities."""
    async_add_entities(
        WebexCESwitch(entry.runtime_data, description) for description in SWITCHES
    )


class WebexCESwitch(WebexCEEntity, SwitchEntity):
    """Switch that follows a status value of the device."""

    entity_description: WebexCESwitchEntityDescription
    _attr_is_on = False

    def __init__(
        self, data: WebexCEData, description: WebexCESwitchEntityDescription
    ) -> None:
        """Initialize the switch."""
        super().__init__(data, description.key)
        self.entity_description = description

    @property
    def icon(self) -> str | None:
        """Return the icon for the current state."""
        if not self.is_on and self.entity_description.icon_off:
            return self.entity_description.icon_off
        return self.entity_description.icon

    async def async_added_to_hass(self) -> None:
        """Subscribe to device feedback when added to hass."""
        await super().async_added_to_hass()
        await self._async_subscribe(
            self.entity_description.status_path, self._handle_feedback
        )

    @callback
    def _handle_feedback(self, params: dict[str, Any], feedback_id: Any) -> None:
        """Handle feedback from the device."""
        _LOGGER.debug("Received feedback for %s: %s", self.unique_id, params)
        # Walk the status path through the nested feedback
        value: Any = params
        for key in self.entity_description.status_path:
            if not isinstance(value, dict):
                _LOGGER.warning("Unexpected feedback format: %s", params)
                return
            value = value.get(key, {})
        self._attr_is_on = self.entity_description.is_on_fn(value)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        path, params = self.entity_description.on_command
        await self._async_command(path, **params)
        # Optimistically update the state, feedback confirms it later
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        path, params = self.entity_description.off_command
        await self._async_command(path, **params)
        # Optimistically update the state, feedback confirms it later
        self._attr_is_on = False
        self.async_write_ha_state()
