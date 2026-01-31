"""Options flow for Tariff Saver."""
from __future__ import annotations

from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_PUBLISH_TIME,
    DEFAULT_PUBLISH_TIME,
    CONF_TARIFF_NAME,
    CONF_BASELINE_TARIFF_NAME,
    CONF_CONSUMPTION_ENERGY_ENTITY,
    CONF_GRADE_T1,
    CONF_GRADE_T2,
    CONF_GRADE_T3,
    CONF_GRADE_T4,
    DEFAULT_GRADE_T1,
    DEFAULT_GRADE_T2,
    DEFAULT_GRADE_T3,
    DEFAULT_GRADE_T4,
    CONF_ENABLE_COST_TRACKING,
    DEFAULT_ENABLE_COST_TRACKING,
)


def _parse_hhmm(value: str) -> str:
    try:
        hh, mm = value.strip().split(":")
        h = int(hh)
        m = int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    except Exception as err:
        raise vol.Invalid("Time must be HH:MM") from err
    raise vol.Invalid("Time must be HH:MM")


class TariffSaverOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for Tariff Saver."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry
        self._errors: dict[str, str] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._errors = {}

            try:
                user_input[CONF_PUBLISH_TIME] = _parse_hhmm(
                    user_input[CONF_PUBLISH_TIME]
                )
            except vol.Invalid:
                self._errors[CONF_PUBLISH_TIME] = "invalid_time"

            try:
                t1 = float(user_input[CONF_GRADE_T1])
                t2 = float(user_input[CONF_GRADE_T2])
                t3 = float(user_input[CONF_GRADE_T3])
                t4 = float(user_input[CONF_GRADE_T4])
                if not (t1 <= t2 <= t3 <= t4):
                    self._errors[CONF_GRADE_T4] = "threshold_order"
            except Exception:
                self._errors[CONF_GRADE_T4] = "threshold_invalid"

            if not self._errors:
                return self.async_create_entry(title="", data=user_input)

        opt = dict(self.config_entry.options)
        dat = dict(self.config_entry.data)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PUBLISH_TIME,
                    default=opt.get(
                        CONF_PUBLISH_TIME,
                        dat.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME),
                    ),
                ): str,

                vol.Optional(
                    CONF_TARIFF_NAME,
                    default=opt.get(CONF_TARIFF_NAME, dat.get(CONF_TARIFF_NAME, "")),
                ): str,

                vol.Optional(
                    CONF_BASELINE_TARIFF_NAME,
                    default=opt.get(
                        CONF_BASELINE_TARIFF_NAME,
                        dat.get(CONF_BASELINE_TARIFF_NAME, ""),
                    ),
                ): str,

                vol.Optional(
                    CONF_CONSUMPTION_ENERGY_ENTITY,
                    default=opt.get(CONF_CONSUMPTION_ENERGY_ENTITY, ""),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),

                vol.Optional(
                    CONF_ENABLE_COST_TRACKING,
                    default=opt.get(
                        CONF_ENABLE_COST_TRACKING, DEFAULT_ENABLE_COST_TRACKING
                    ),
                ): bool,

                vol.Required(
                    CONF_GRADE_T1, default=opt.get(CONF_GRADE_T1, DEFAULT_GRADE_T1)
                ): vol.Coerce(float),
                vol.Required(
                    CONF_GRADE_T2, default=opt.get(CONF_GRADE_T2, DEFAULT_GRADE_T2)
                ): vol.Coerce(float),
                vol.Required(
                    CONF_GRADE_T3, default=opt.get(CONF_GRADE_T3, DEFAULT_GRADE_T3)
                ): vol.Coerce(float),
                vol.Required(
                    CONF_GRADE_T4, default=opt.get(CONF_GRADE_T4, DEFAULT_GRADE_T4)
                ): vol.Coerce(float),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=self._errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return TariffSaverOptionsFlowHandler(config_entry)
