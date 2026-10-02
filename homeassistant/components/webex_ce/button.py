"""Button platform for Webex CE devices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import WebexCEEntity
from .models import WebexCEConfigEntry, WebexCEData

# Buttons are user-triggered, no parallel update concerns
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class WebexCEButtonEntityDescription(ButtonEntityDescription):
    """Describes a Webex CE button."""

    command: list[str]


BUTTONS: tuple[WebexCEButtonEntityDescription, ...] = (
    WebexCEButtonEntityDescription(
        key="accept_call",
        translation_key="accept_call",
        icon="mdi:phone-check",
        command=["Call", "Accept"],
    ),
    WebexCEButtonEntityDescription(
        key="reject_call",
        translation_key="reject_call",
        icon="mdi:phone-cancel",
        command=["Call", "Reject"],
    ),
    WebexCEButtonEntityDescription(
        key="disconnect_call",
        translation_key="disconnect_call",
        icon="mdi:phone-hangup",
        command=["Call", "Disconnect"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WebexCEConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Webex CE button entities."""
    async_add_entities(
        WebexCEButton(entry.runtime_data, description) for description in BUTTONS
    )


class WebexCEButton(WebexCEEntity, ButtonEntity):
    """Button that runs a call control command."""

    entity_description: WebexCEButtonEntityDescription

    def __init__(
        self, data: WebexCEData, description: WebexCEButtonEntityDescription
    ) -> None:
        """Initialize the button."""
        super().__init__(data, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Run the command of the button."""
        await self._async_command(self.entity_description.command)
