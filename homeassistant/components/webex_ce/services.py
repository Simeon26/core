"""Service actions for the Webex devices integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import voluptuous as vol
import xows

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_extract_config_entry_ids
from homeassistant.helpers.typing import VolSchemaType

from .const import DOMAIN

if TYPE_CHECKING:
    from .client import WebexCEClient
    from .models import WebexCEConfigEntry

SERVICE_DIAL = "dial"
SERVICE_SEND_DTMF = "send_dtmf"
SERVICE_CAMERA_PRESET_ACTIVATE = "camera_preset_activate"
SERVICE_CAMERA_PRESET_STORE = "camera_preset_store"
SERVICE_CAMERA_POSITION_SET = "camera_position_set"
SERVICE_CAMERA_RAMP = "camera_ramp"
SERVICE_CAMERA_RAMP_STOP = "camera_ramp_stop"
SERVICE_DISPLAY_MESSAGE = "display_message"
SERVICE_CLEAR_MESSAGE = "clear_message"
SERVICE_DISPLAY_WEBVIEW = "display_webview"
SERVICE_CLOSE_WEBVIEW = "close_webview"

ATTR_CAMERA_ID = "camera_id"
ATTR_DIRECTION = "direction"
ATTR_DTMF = "dtmf"
ATTR_DURATION = "duration"
ATTR_MODE = "mode"
ATTR_NAME = "name"
ATTR_NUMBER = "number"
ATTR_PAN = "pan"
ATTR_PRESET_ID = "preset_id"
ATTR_SPEED = "speed"
ATTR_TEXT = "text"
ATTR_TILT = "tilt"
ATTR_TITLE = "title"
ATTR_URL = "url"
ATTR_ZOOM = "zoom"

# Map the user-facing ramp direction to the xAPI axis and value.
RAMP_DIRECTIONS: dict[str, tuple[str, str]] = {
    "Up": ("Tilt", "Up"),
    "Down": ("Tilt", "Down"),
    "Left": ("Pan", "Left"),
    "Right": ("Pan", "Right"),
    "ZoomIn": ("Zoom", "In"),
    "ZoomOut": ("Zoom", "Out"),
}

_CAMERA_ID = vol.All(vol.Coerce(int), vol.Range(min=1, max=7))
_PRESET_ID = vol.All(vol.Coerce(int), vol.Range(min=1, max=35))
_PAN_TILT = vol.All(vol.Coerce(int), vol.Range(min=-65535, max=65535))

SCHEMAS: dict[str, VolSchemaType] = {
    SERVICE_DIAL: cv.make_entity_service_schema({vol.Required(ATTR_NUMBER): cv.string}),
    SERVICE_SEND_DTMF: cv.make_entity_service_schema(
        {vol.Required(ATTR_DTMF): cv.string}
    ),
    SERVICE_CAMERA_PRESET_ACTIVATE: cv.make_entity_service_schema(
        {vol.Required(ATTR_PRESET_ID): _PRESET_ID}
    ),
    SERVICE_CAMERA_PRESET_STORE: cv.make_entity_service_schema(
        {
            vol.Required(ATTR_PRESET_ID): _PRESET_ID,
            vol.Optional(ATTR_CAMERA_ID, default=1): _CAMERA_ID,
            vol.Optional(ATTR_NAME): cv.string,
        }
    ),
    SERVICE_CAMERA_POSITION_SET: cv.make_entity_service_schema(
        {
            vol.Optional(ATTR_CAMERA_ID, default=1): _CAMERA_ID,
            vol.Optional(ATTR_PAN): _PAN_TILT,
            vol.Optional(ATTR_TILT): _PAN_TILT,
            vol.Optional(ATTR_ZOOM): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=65535)
            ),
        }
    ),
    SERVICE_CAMERA_RAMP: cv.make_entity_service_schema(
        {
            vol.Optional(ATTR_CAMERA_ID, default=1): _CAMERA_ID,
            vol.Required(ATTR_DIRECTION): vol.In(list(RAMP_DIRECTIONS)),
            vol.Optional(ATTR_SPEED, default=5): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=15)
            ),
        }
    ),
    SERVICE_CAMERA_RAMP_STOP: cv.make_entity_service_schema(
        {vol.Optional(ATTR_CAMERA_ID, default=1): _CAMERA_ID}
    ),
    SERVICE_DISPLAY_MESSAGE: cv.make_entity_service_schema(
        {
            vol.Required(ATTR_TEXT): cv.string,
            vol.Optional(ATTR_DURATION, default=10): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=3600)
            ),
        }
    ),
    SERVICE_CLEAR_MESSAGE: cv.make_entity_service_schema({}),
    SERVICE_DISPLAY_WEBVIEW: cv.make_entity_service_schema(
        {
            vol.Required(ATTR_URL): cv.string,
            vol.Optional(ATTR_TITLE): cv.string,
            vol.Optional(ATTR_MODE, default="Modal"): vol.In(["Modal", "Fullscreen"]),
        }
    ),
    SERVICE_CLOSE_WEBVIEW: cv.make_entity_service_schema({}),
}


def _dial(data: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    return ["Dial"], {"Number": data[ATTR_NUMBER]}


def _send_dtmf(data: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    return ["Call", "DTMFSend"], {"DTMFString": data[ATTR_DTMF]}


def _preset_activate(data: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    return ["Camera", "Preset", "Activate"], {"PresetId": data[ATTR_PRESET_ID]}


def _preset_store(data: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    # CameraId is a required parameter of xCommand Camera Preset Store
    params: dict[str, Any] = {
        "CameraId": data[ATTR_CAMERA_ID],
        "PresetId": data[ATTR_PRESET_ID],
    }
    if ATTR_NAME in data:
        params["Name"] = data[ATTR_NAME]
    return ["Camera", "Preset", "Store"], params


def _position_set(data: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    params: dict[str, Any] = {"CameraId": data[ATTR_CAMERA_ID]}
    for attr, param in ((ATTR_PAN, "Pan"), (ATTR_TILT, "Tilt"), (ATTR_ZOOM, "Zoom")):
        if attr in data:
            params[param] = data[attr]
    return ["Camera", "PositionSet"], params


def _ramp(data: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    # xAPI expects e.g. "Tilt: Up TiltSpeed: 5" rather than the direction name
    axis, value = RAMP_DIRECTIONS[data[ATTR_DIRECTION]]
    return ["Camera", "Ramp"], {
        "CameraId": data[ATTR_CAMERA_ID],
        axis: value,
        f"{axis}Speed": data[ATTR_SPEED],
    }


def _ramp_stop(data: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    return ["Camera", "Ramp"], {
        "CameraId": data[ATTR_CAMERA_ID],
        "Pan": "Stop",
        "Tilt": "Stop",
        "Zoom": "Stop",
    }


def _display_message(data: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    return ["UserInterface", "Message", "TextLine", "Display"], {
        "Text": data[ATTR_TEXT],
        "Duration": data[ATTR_DURATION],
    }


def _clear_message(data: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    return ["UserInterface", "Message", "TextLine", "Clear"], {}


def _display_webview(data: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    params: dict[str, Any] = {"Url": data[ATTR_URL], "Mode": data[ATTR_MODE]}
    if title := data.get(ATTR_TITLE):
        params["Title"] = title
    return ["UserInterface", "WebView", "Display"], params


def _close_webview(data: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    return ["UserInterface", "WebView", "Clear"], {}


type _CommandBuilder = Callable[[dict[str, Any]], tuple[list[str], dict[str, Any]]]

COMMANDS: dict[str, _CommandBuilder] = {
    SERVICE_DIAL: _dial,
    SERVICE_SEND_DTMF: _send_dtmf,
    SERVICE_CAMERA_PRESET_ACTIVATE: _preset_activate,
    SERVICE_CAMERA_PRESET_STORE: _preset_store,
    SERVICE_CAMERA_POSITION_SET: _position_set,
    SERVICE_CAMERA_RAMP: _ramp,
    SERVICE_CAMERA_RAMP_STOP: _ramp_stop,
    SERVICE_DISPLAY_MESSAGE: _display_message,
    SERVICE_CLEAR_MESSAGE: _clear_message,
    SERVICE_DISPLAY_WEBVIEW: _display_webview,
    SERVICE_CLOSE_WEBVIEW: _close_webview,
}


async def _async_get_target_clients(call: ServiceCall) -> list[WebexCEClient]:
    """Return the clients of the loaded devices targeted by a service call."""
    target_entry_ids = await async_extract_config_entry_ids(call)
    entries: list[WebexCEConfigEntry] = [
        entry
        for entry in call.hass.config_entries.async_loaded_entries(DOMAIN)
        if entry.entry_id in target_entry_ids
    ]
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_target",
        )
    return [entry.runtime_data.client for entry in entries]


async def _async_handle_service(call: ServiceCall) -> None:
    """Run the xAPI command for a service call on every targeted device."""
    path, params = COMMANDS[call.service](dict(call.data))
    clients = await _async_get_target_clients(call)
    results = await asyncio.gather(
        *(client.xcommand(path, **params) for client in clients),
        return_exceptions=True,
    )
    errors: dict[str, BaseException] = {}
    for client, result in zip(clients, results, strict=True):
        if isinstance(result, (xows.XoWSError, OSError)):
            errors[client.host] = result
        elif isinstance(result, BaseException):
            raise result
    if errors:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={
                "action": call.service,
                "host": ", ".join(errors),
                "error": "; ".join(
                    dict.fromkeys(
                        str(err) or type(err).__name__ for err in errors.values()
                    )
                ),
            },
        ) from next(iter(errors.values()))


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Webex devices service actions."""
    for service, schema in SCHEMAS.items():
        hass.services.async_register(
            DOMAIN, service, _async_handle_service, schema=schema
        )
