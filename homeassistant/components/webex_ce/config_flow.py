"""Config flow for the Webex devices integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol
import xows

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import HomeAssistantError

from .client import WebexCEClient, WebexCEDeviceInfo
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(data: Mapping[str, Any]) -> WebexCEDeviceInfo:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    client = WebexCEClient(data[CONF_HOST], data[CONF_USERNAME], data[CONF_PASSWORD])

    try:
        await client.connect()
    except xows.AuthenticationFailure as err:
        raise InvalidAuth from err
    except (xows.XoWSError, OSError) as err:
        _LOGGER.debug("Could not connect to %s: %s", data[CONF_HOST], err)
        raise CannotConnect from err

    # Always close the connection, even if reading device info fails
    try:
        return await client.get_device_info()
    except (xows.XoWSError, OSError) as err:
        _LOGGER.debug("Could not read device info of %s: %s", data[CONF_HOST], err)
        raise CannotConnect from err
    finally:
        await client.disconnect()


class WebexCEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Webex devices."""

    VERSION = 1
    MINOR_VERSION = 1

    async def _async_validate(
        self, data: Mapping[str, Any], errors: dict[str, str]
    ) -> WebexCEDeviceInfo | None:
        """Validate the input, adding any error to errors."""
        try:
            return await validate_input(data)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None and (
            info := await self._async_validate(user_input, errors)
        ):
            # Use serial number as unique ID to prevent duplicates
            await self.async_set_unique_id(info.serial)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(title=info.name, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle the device rejecting the stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials of the device."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None and (
            info := await self._async_validate(
                {**reauth_entry.data, **user_input}, errors
            )
        ):
            await self.async_set_unique_id(info.serial)
            self._abort_if_unique_id_mismatch(reason="wrong_device")
            return self.async_update_reload_and_abort(
                reauth_entry, data_updates=user_input
            )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_REAUTH_DATA_SCHEMA,
                {CONF_USERNAME: reauth_entry.data[CONF_USERNAME]},
            ),
            description_placeholders={CONF_HOST: reauth_entry.data[CONF_HOST]},
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
