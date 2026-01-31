"""Config flow for Tariff Saver."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN


class TariffSaverConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tariff Saver."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required("tariff_name"): str,
                }
            )
            return self.async_show_form(step_id="user", data_schema=schema)

        tariff_name = user_input["tariff_name"].strip()
        return self.async_create_entry(
            title=f"Tariff Saver ({tariff_name})",
            data={"tariff_name": tariff_name},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return TariffSaverOptionsFlow(config_entry)


class TariffSaverOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Tariff Saver."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        return self.async_create_entry(title="", data={})
