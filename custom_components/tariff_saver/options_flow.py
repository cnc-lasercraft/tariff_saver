"""Options flow for Tariff Saver (TEST BUILD).

This file is intentionally minimal and contains a visible marker in the UI
so we can confirm Home Assistant is loading the correct options flow code.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries


OPT_TEST_FLAG = "ts_test_flag"


class TariffSaverOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle an options flow for Tariff Saver."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            # Keep all existing options and just update our test flag
            data = dict(self._entry.options)
            data.update(user_input)
            return self.async_create_entry(title="", data=data)

        schema = vol.Schema(
            {
                # VERY visible marker label/key
                vol.Required(OPT_TEST_FLAG, default=self._entry.options.get(OPT_TEST_FLAG, False)): bool,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )
