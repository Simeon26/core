"""Base entity for the Webex devices integration."""

from __future__ import annotations

from typing import Any

import xows

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity

from .client import FeedbackCallback
from .const import DOMAIN
from .models import WebexCEData


class WebexCEEntity(Entity):
    """Base class for entities of a Webex device.

    The device pushes changes through feedback subscriptions, so entities
    are not polled.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, data: WebexCEData, key: str) -> None:
        """Initialize the entity."""
        self._client = data.client
        self._attr_device_info = data.device_info
        self._attr_unique_id = f"{data.serial}_{key}"

    @property
    def available(self) -> bool:
        """Return if the device is connected."""
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        """Follow the connection state of the device."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._client.add_connection_listener(self.async_write_ha_state)
        )

    async def _async_subscribe(
        self, path: list[str], callback: FeedbackCallback, suffix: str | None = None
    ) -> None:
        """Subscribe to feedback for a status path of the device."""
        feedback_id = f"{self.unique_id}_{suffix}" if suffix else str(self.unique_id)
        await self._client.subscribe_feedback(feedback_id, path, callback)

    async def _async_command(self, path: list[str], **params: Any) -> Any:
        """Run a command on the device."""
        try:
            return await self._client.xcommand(path, **params)
        except (xows.XoWSError, OSError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={
                    "action": " ".join(path),
                    "host": self._client.host,
                    "error": str(err) or type(err).__name__,
                },
            ) from err
