"""Tests for the Webex devices integration."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant


async def send_feedback(
    hass: HomeAssistant,
    client: MagicMock,
    path: list[str],
    params: dict[str, Any],
) -> None:
    """Send xAPI feedback to every subscriber of a status path."""
    callbacks = client.feedback_callbacks.get(tuple(path))
    assert callbacks, f"Nothing subscribed to {path}"
    for callback in callbacks:
        callback(params, "feedback_id")
    await hass.async_block_till_done()


async def set_connected(
    hass: HomeAssistant, client: MagicMock, connected: bool
) -> None:
    """Change the connection state of a mocked client and tell its listeners."""
    client.connected = connected
    for listener in list(client.connection_listeners):
        listener()
    await hass.async_block_till_done()
