"""Data models for the Webex devices integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .client import WebexCEClient

type WebexCEConfigEntry = ConfigEntry[WebexCEData]


@dataclass
class WebexCEData:
    """Runtime data of a Webex device config entry."""

    client: WebexCEClient
    serial: str
    device_info: DeviceInfo
