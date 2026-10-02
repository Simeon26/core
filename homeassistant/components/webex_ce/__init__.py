"""The Webex devices integration."""

from __future__ import annotations

import xows

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import ConfigType

from .client import WebexCEClient
from .const import DOMAIN
from .models import WebexCEConfigEntry, WebexCEData
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Sensors that were replaced by binary sensors with the same key
REPLACED_SENSORS = ("recording", "streaming")

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

__all__ = ["WebexCEConfigEntry", "WebexCEData"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Webex devices integration."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WebexCEConfigEntry) -> bool:
    """Set up Webex devices from a config entry."""
    host = entry.data[CONF_HOST]
    client = WebexCEClient(host, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    try:
        await client.connect()
    except xows.AuthenticationFailure as err:
        raise ConfigEntryAuthFailed(f"Invalid credentials for {host}") from err
    except (xows.XoWSError, OSError) as err:
        raise ConfigEntryNotReady(f"Failed to connect to {host}: {err}") from err

    try:
        device_info = await client.get_device_info()
    except (xows.XoWSError, OSError) as err:
        await client.disconnect()
        raise ConfigEntryNotReady(
            f"Failed to read device information from {host}: {err}"
        ) from err

    entry.runtime_data = WebexCEData(
        client=client,
        serial=device_info.serial,
        device_info=DeviceInfo(
            identifiers={(DOMAIN, device_info.serial)},
            name=entry.title,
            manufacturer="Cisco",
            model=device_info.product,
            sw_version=device_info.software_version,
        ),
    )

    _async_remove_replaced_sensors(hass, device_info.serial)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_create_background_task(
        hass,
        _async_maintain_connection(hass, entry),
        f"{DOMAIN} connection to {host}",
    )

    return True


async def _async_maintain_connection(
    hass: HomeAssistant, entry: WebexCEConfigEntry
) -> None:
    """Keep the connection to the device alive while the entry is loaded."""
    try:
        await entry.runtime_data.client.async_maintain_connection()
    except xows.AuthenticationFailure:
        entry.async_start_reauth(hass)


def _async_remove_replaced_sensors(hass: HomeAssistant, serial: str) -> None:
    """Remove sensors that were replaced by binary sensors."""
    entity_registry = er.async_get(hass)
    for key in REPLACED_SENSORS:
        if entity_id := entity_registry.async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{serial}_{key}"
        ):
            entity_registry.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: WebexCEConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        await entry.runtime_data.client.disconnect()

    return unload_ok
