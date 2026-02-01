"""Options flow for Tariff Saver."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector


# --- Option keys (kept local for now; we can move to const.py later) ---
OPT_PRICE_MODE = "price_mode"  # "fetch" | "import"
OPT_IMPORT_PROVIDER = "import_provider"  # currently only "ekz_api"
OPT_SOURCE_INTERVAL_MIN = "source_interval_minutes"  # 15 | 60
OPT_NORMALIZATION_MODE = "normalization_mode"  # "repeat" (60 -> 15)

OPT_IMPORT_ENTITY_DYN = "import_entity_dyn"  # entity_id
OPT_BASELINE_MODE = "baseline_mode"  # "fixed" | "entity"
OPT_BASELINE_VALUE = "baseline_value"  # float (CHF/kWh after scaling)
OPT_BASELINE_ENTITY = "baseline_entity"  # entity_id

OPT_PRICE_SCALE = "price_scale"  # float multiplier
OPT_IGNORE_ZERO_PRICES = "ignore_zero_prices"  # bool


# --- Defaults ---
DEFAULT_PRICE_MODE = "fetch"
DEFAULT_IMPORT_PROVIDER = "ekz_api"
DEFAULT_SOURCE_INTERVAL_MIN = 15
DEFAULT_NORMALIZATION_MODE = "repeat"

DEFAULT_BASELINE_MODE = "fixed"
DEFAULT_BASELINE_VALUE = 0.0

DEFAULT_PRICE_SCALE = 1.0
DEFAULT_IGNORE_ZERO_PRICES = True


def _sensor_entity_selector() -> selector.EntitySelector:
    """Entity selector limited to sensor domain (HA 2026.x compatible)."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            filter=selector.EntityFilterSelectorConfig(domain=["sensor"])
        )
    )


class TariffSaverOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle an options flow for Tariff Saver."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage options."""
        if user_input is not None:
            # Persist exactly what user provided (clean minimal)
            opts: dict = dict(self._entry.options)

            opts[OPT_PRICE_MODE] = user_input[OPT_PRICE_MODE]
            opts[OPT_IMPORT_PROVIDER] = user_input[OPT_IMPORT_PROVIDER]
            opts[OPT_SOURCE_INTERVAL_MIN] = int(user_input[OPT_SOURCE_INTERVAL_MIN])
            opts[OPT_NORMALIZATION_MODE] = user_input[OPT_NORMALIZATION_MODE]

            opts[OPT_IMPORT_ENTITY_DYN] = user_input.get(OPT_IMPORT_ENTITY_DYN)

            opts[OPT_BASELINE_MODE] = user_input[OPT_BASELINE_MODE]
            opts[OPT_BASELINE_VALUE] = float(user_input.get(OPT_BASELINE_VALUE, 0.0))
            opts[OPT_BASELINE_ENTITY] = user_input.get(OPT_BASELINE_ENTITY)

            opts[OPT_PRICE_SCALE] = float(user_input.get(OPT_PRICE_SCALE, 1.0))
            opts[OPT_IGNORE_ZERO_PRICES] = bool(
                user_input.get(OPT_IGNORE_ZERO_PRICES, True)
            )

            return self.async_create_entry(title="", data=opts)

        # Existing options / defaults
        current = dict(self._entry.options)

        price_mode = current.get(OPT_PRICE_MODE, DEFAULT_PRICE_MODE)
        import_provider = current.get(OPT_IMPORT_PROVIDER, DEFAULT_IMPORT_PROVIDER)
        source_interval = int(current.get(OPT_SOURCE_INTERVAL_MIN, DEFAULT_SOURCE_INTERVAL_MIN))
        normalization_mode = current.get(OPT_NORMALIZATION_MODE, DEFAULT_NORMALIZATION_MODE)

        import_entity_dyn = current.get(OPT_IMPORT_ENTITY_DYN)

        baseline_mode = current.get(OPT_BASELINE_MODE, DEFAULT_BASELINE_MODE)
        baseline_value = float(current.get(OPT_BASELINE_VALUE, DEFAULT_BASELINE_VALUE))
        baseline_entity = current.get(OPT_BASELINE_ENTITY)

        price_scale = float(current.get(OPT_PRICE_SCALE, DEFAULT_PRICE_SCALE))
        ignore_zero = bool(current.get(OPT_IGNORE_ZERO_PRICES, DEFAULT_IGNORE_ZERO_PRICES))

        schema = vol.Schema(
            {
                # 1) Price source: built-in fetch vs import entity
                vol.Required(
                    OPT_PRICE_MODE,
                    default=price_mode,
                ): vol.In(
                    {
                        "fetch": "EKZ API (integriert)",
                        "import": "Import aus Home-Assistant Entität",
                    }
                ),

                # Dropdown for provider catalog (currently only EKZ)
                vol.Required(
                    OPT_IMPORT_PROVIDER,
                    default=import_provider,
                ): vol.In(
                    {
                        "ekz_api": "EKZ API",
                    }
                ),

                # 2) Source interval (what the upstream tariff changes in)
                vol.Required(
                    OPT_SOURCE_INTERVAL_MIN,
                    default=source_interval,
                ): vol.In(
                    {
                        15: "15 Minuten",
                        60: "60 Minuten (Stundenpreise)",
                    }
                ),

                # Normalization to our internal 15-min slots
                vol.Required(
                    OPT_NORMALIZATION_MODE,
                    default=normalization_mode,
                ): vol.In(
                    {
                        "repeat": "Stundenpreis auf 4×15min replizieren",
                    }
                ),

                # 3) Import entity (dynamic price)
                vol.Optional(
                    OPT_IMPORT_ENTITY_DYN,
                    default=import_entity_dyn,
                ): _sensor_entity_selector(),

                # 4) Baseline configuration (for real savings)
                vol.Required(
                    OPT_BASELINE_MODE,
                    default=baseline_mode,
                ): vol.In(
                    {
                        "fixed": "Baseline: fixer Wert",
                        "entity": "Baseline: Home-Assistant Entität",
                    }
                ),
                vol.Optional(
                    OPT_BASELINE_VALUE,
                    default=baseline_value,
                ): vol.Coerce(float),
                vol.Optional(
                    OPT_BASELINE_ENTITY,
                    default=baseline_entity,
                ): _sensor_entity_selector(),

                # 5) Scale (Rp/kWh, ct/kWh, CHF/MWh, ...)
                vol.Required(
                    OPT_PRICE_SCALE,
                    default=price_scale,
                ): vol.Coerce(float),

                vol.Required(
                    OPT_IGNORE_ZERO_PRICES,
                    default=ignore_zero,
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
