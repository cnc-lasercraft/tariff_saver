"""Options flow for Tariff Saver."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector


# --- Option keys (kept local for now; we can move to const.py later) ---
OPT_PRICE_MODE = "price_mode"  # "fetch" | "import"
OPT_IMPORT_PROVIDER = "import_provider"  # future-proof; currently only "ekz_api"
OPT_SOURCE_INTERVAL_MIN = "source_interval_minutes"  # 15 | 60
OPT_NORMALIZATION_MODE = "normalization_mode"  # "repeat" (60 -> 15)

OPT_IMPORT_ENTITY_DYN = "import_entity_dyn"  # entity_id (tariff price)
OPT_IMPORT_ENTITY_BASE = "import_entity_base"  # entity_id (baseline price)

OPT_BASELINE_MODE = "baseline_mode"  # "api" | "entity" | "fixed" | "none"
OPT_BASELINE_FIXED_RP_KWH = "baseline_fixed_rp_per_kwh"
OPT_BASELINE_ENTITY = "baseline_entity"

OPT_CONSUMPTION_ENERGY_ENTITY = "consumption_energy_entity"
OPT_PUBLISH_TIME = "publish_time"  # HH:MM (local time)

# Price normalization / units
OPT_PRICE_SCALE = "price_scale"  # multiplier applied to source values to get CHF/kWh
OPT_IGNORE_ZERO_PRICES = "ignore_zero_prices"

# PV / Solar (new)
OPT_SOLAR_INSTALLED = "solar_installed"
OPT_USE_SOLCAST = "use_solcast"
OPT_SOLAR_COST_RP_KWH = "solar_cost_rp_per_kwh"  # numeric input (Rp/kWh)


def _sensor_entity_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(filter=selector.EntityFilterSelectorConfig(domain=["sensor"]))
    )


class TariffSaverOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle an options flow for Tariff Saver."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage options."""
        if user_input is not None:
            # If solar not installed, force-disable Solcast
            solar_installed = bool(user_input.get(OPT_SOLAR_INSTALLED, False))
            if not solar_installed:
                user_input[OPT_USE_SOLCAST] = False

            return self.async_create_entry(title="", data=user_input)

        # ---- defaults (existing -> fallback) ----
        opts = dict(self._entry.options)
        data = dict(self._entry.data)

        price_mode = opts.get(OPT_PRICE_MODE, "fetch")
        import_provider = opts.get(OPT_IMPORT_PROVIDER, "ekz_api")

        source_interval = int(opts.get(OPT_SOURCE_INTERVAL_MIN, 15))
        normalization = opts.get(OPT_NORMALIZATION_MODE, "repeat")

        import_entity_dyn = opts.get(OPT_IMPORT_ENTITY_DYN, "")
        import_entity_base = opts.get(OPT_IMPORT_ENTITY_BASE, "")

        baseline_mode = opts.get(OPT_BASELINE_MODE, "api")
        baseline_fixed = float(opts.get(OPT_BASELINE_FIXED_RP_KWH, 0.0))
        baseline_entity = opts.get(OPT_BASELINE_ENTITY, "")

        consumption_entity = opts.get(OPT_CONSUMPTION_ENERGY_ENTITY, data.get(OPT_CONSUMPTION_ENERGY_ENTITY, ""))
        publish_time = opts.get(OPT_PUBLISH_TIME, data.get(OPT_PUBLISH_TIME, "18:15"))

        price_scale = float(opts.get(OPT_PRICE_SCALE, 1.0))
        ignore_zero = bool(opts.get(OPT_IGNORE_ZERO_PRICES, True))

        solar_installed = bool(opts.get(OPT_SOLAR_INSTALLED, False))
        use_solcast = bool(opts.get(OPT_USE_SOLCAST, False))
        solar_cost_rp = float(opts.get(OPT_SOLAR_COST_RP_KWH, 0.0))

        # ---- schema base ----
        schema_dict: dict[vol.Marker, object] = {
            # 1) Price source
            vol.Required(
                OPT_PRICE_MODE,
                default=price_mode,
            ): vol.In(
                {
                    "fetch": "EKZ API",
                    "import": "Import from existing entities",
                }
            ),
        }

        # Price mode details
        if price_mode == "import":
            schema_dict.update(
                {
                    vol.Required(
                        OPT_IMPORT_ENTITY_DYN,
                        default=import_entity_dyn,
                    ): _sensor_entity_selector(),
                    vol.Optional(
                        OPT_IMPORT_PROVIDER,
                        default=import_provider,
                    ): vol.In(
                        {
                            "ekz_api": "EKZ API (placeholder)",
                        }
                    ),
                }
            )
        else:
            # fetch-mode: provider placeholder (future-proof)
            schema_dict.update(
                {
                    vol.Optional(
                        OPT_IMPORT_PROVIDER,
                        default=import_provider,
                    ): vol.In(
                        {
                            "ekz_api": "EKZ API",
                        }
                    )
                }
            )

        # 2) Source interval & normalization
        schema_dict.update(
            {
                vol.Required(
                    OPT_SOURCE_INTERVAL_MIN,
                    default=source_interval,
                ): vol.In({15: "15 minutes", 60: "60 minutes"}),
                vol.Required(
                    OPT_NORMALIZATION_MODE,
                    default=normalization,
                ): vol.In(
                    {
                        "repeat": "Repeat (60→15)",
                    }
                ),
            }
        )

        # 3) Baseline
        schema_dict.update(
            {
                vol.Required(
                    OPT_BASELINE_MODE,
                    default=baseline_mode,
                ): vol.In(
                    {
                        "api": "From API / source",
                        "entity": "From entity",
                        "fixed": "Fixed value",
                        "none": "No baseline",
                    }
                )
            }
        )

        if baseline_mode == "entity":
            schema_dict.update(
                {
                    vol.Required(
                        OPT_BASELINE_ENTITY,
                        default=baseline_entity,
                    ): _sensor_entity_selector()
                }
            )
        elif baseline_mode == "fixed":
            schema_dict.update(
                {
                    vol.Required(
                        OPT_BASELINE_FIXED_RP_KWH,
                        default=baseline_fixed,
                    ): vol.Coerce(float)
                }
            )
        elif baseline_mode == "api":
            # Optional baseline import entity (only used when price_mode==import or later providers)
            schema_dict.update(
                {
                    vol.Optional(
                        OPT_IMPORT_ENTITY_BASE,
                        default=import_entity_base,
                    ): _sensor_entity_selector()
                }
            )

        # 4) Consumption / publish time
        schema_dict.update(
            {
                vol.Optional(
                    OPT_CONSUMPTION_ENERGY_ENTITY,
                    default=consumption_entity,
                ): _sensor_entity_selector(),
                vol.Optional(
                    OPT_PUBLISH_TIME,
                    default=publish_time,
                ): str,
            }
        )

        # 5) Scaling / data hygiene
        schema_dict.update(
            {
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

        # 6) Solar / Solcast (new)
        schema_dict.update(
            {
                vol.Required(
                    OPT_SOLAR_INSTALLED,
                    default=solar_installed,
                ): bool,
            }
        )

        if solar_installed:
            schema_dict.update(
                {
                    vol.Required(
                        OPT_USE_SOLCAST,
                        default=use_solcast,
                    ): bool,
                    vol.Required(
                        OPT_SOLAR_COST_RP_KWH,
                        default=solar_cost_rp,
                    ): vol.Coerce(float),
                }
            )

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
