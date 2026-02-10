"""Config flow for Tariff Saver.

Change:
- myEKZ mode no longer asks for ems_instance_id
- we auto-generate a stable, unique ems_instance_id and store it in entry.data

Rationale:
- EKZ requires ems_instance_id to be unique and persistent.
- Best practice is to use a serial number of the EMS instance.
- In Home Assistant we do not have a real EMS serial, so we generate one once.

IMPORTANT:
- This does NOT rename entities.
- Public mode is unchanged.
"""
from __future__ import annotations

import uuid

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .const import DOMAIN, DEFAULT_PUBLISH_TIME, CONF_PUBLISH_TIME

# Modes
MODE_PUBLIC = "public"
MODE_MYEKZ = "myekz"


def _generate_ems_instance_id() -> str:
    """Generate a unique, persistent EMS instance id.

    EKZ requirement:
    - unique + persistent
    - best practice: serial number of the EMS

    We generate a UUID once and store it in the config entry.
    """
    return f"ha-941f0293befd4cc894168304a9537863"


class TariffSaverConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tariff Saver."""

    VERSION = 2

    def __init__(self) -> None:
        self._name: str | None = None
        self._mode: str | None = None

    async def async_step_user(self, user_input=None):
        """Initial step: choose integration name."""
        if user_input is not None:
            self._name = user_input[CONF_NAME]
            return await self.async_step_mode()

        schema = vol.Schema({vol.Required(CONF_NAME): str})
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_mode(self, user_input=None):
        """Choose authentication mode."""
        if user_input is not None:
            self._mode = user_input["mode"]
            if self._mode == MODE_PUBLIC:
                return await self.async_step_public()
            return await self.async_step_myekz()

        schema = vol.Schema(
            {
                vol.Required("mode", default=MODE_PUBLIC): vol.In(
                    {
                        MODE_PUBLIC: "Public (no login)",
                        MODE_MYEKZ: "myEKZ login",
                    }
                )
            }
        )
        return self.async_show_form(step_id="mode", data_schema=schema)

    async def async_step_public(self, user_input=None):
        """Public (no-login) configuration."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._name,
                data={
                    CONF_NAME: self._name,
                    "mode": MODE_PUBLIC,
                    "tariff_name": user_input["tariff_name"],
                    "baseline_tariff_name": user_input.get("baseline_tariff_name"),
                    CONF_PUBLISH_TIME: user_input.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME),
                },
            )

        schema = vol.Schema(
            {
                vol.Required("tariff_name"): str,
                vol.Optional("baseline_tariff_name", default="electricity_standard"): str,
                vol.Optional(CONF_PUBLISH_TIME, default=DEFAULT_PUBLISH_TIME): str,  # HH:MM
            }
        )
        return self.async_show_form(step_id="public", data_schema=schema)

    async def async_step_myekz(self, user_input=None):
        """myEKZ configuration.

        NOTE:
        We only ask for redirect_uri. ems_instance_id is auto-generated.
        """
        if user_input is not None:
            redirect_uri = user_input["redirect_uri"].strip()

            # Auto-generate a unique EMS instance id for this HA installation
            ems_instance_id = _generate_ems_instance_id()

            return self.async_create_entry(
                title=self._name,
                data={
                    CONF_NAME: self._name,
                    "mode": MODE_MYEKZ,

                    # required by EKZ protected endpoints
                    "ems_instance_id": ems_instance_id,
                    "redirect_uri": redirect_uri,

                    # placeholders to keep existing coordinator logic stable
                    "tariff_name": "myEKZ",
                    "baseline_tariff_name": None,

                    CONF_PUBLISH_TIME: user_input.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME),
                },
            )

        # Prefill redirect_uri from configured external_url if present
        default_redirect = (self.hass.config.external_url or "").rstrip("/") + "/"
        schema = vol.Schema(
            {
                vol.Required("redirect_uri", default=default_redirect or "https://"): str,
                vol.Optional(CONF_PUBLISH_TIME, default=DEFAULT_PUBLISH_TIME): str,
            }
        )
        return self.async_show_form(step_id="myekz", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler.

        IMPORTANT:
        Options flow lives in options_flow.py. Do NOT define it in this file,
        otherwise changes to options_flow.py will never be used.
        """
        from .options_flow import TariffSaverOptionsFlowHandler

        return TariffSaverOptionsFlowHandler(config_entry)
