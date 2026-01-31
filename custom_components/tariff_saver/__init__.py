"""Tariff Saver integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change

from .api import EkzTariffApi
from .const import DOMAIN, CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME
from .coordinator import TariffSaverCoordinator

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Tariff Saver (YAML not used)."""
    return True

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor"]

# Must match the highest ConfigFlow.VERSION you have shipped/used
CONFIG_VERSION = 2
CONFIG_MINOR_VERSION = 0


def _parse_hhmm(value: str) -> tuple[int, int]:
    """Parse 'HH:MM' -> (hour, minute). Fallback to default on bad input."""
    try:
        hh, mm = value.strip().split(":")
        h = int(hh)
        m = int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass

    # fallback to integration default (EKZ publish time)
    return _parse_hhmm(DEFAULT_PUBLISH_TIME) if value != DEFAULT_PUBLISH_TIME else (18, 15)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries.

    This prevents HA from refusing to load if stored entries have older/newer versions.
    """
    _LOGGER.debug(
        "Migrating %s entry %s from version %s.%s",
        DOMAIN,
        entry.entry_id,
        entry.version,
        entry.minor_version,
    )

    data = dict(entry.data)
    options = dict(entry.options)

    # v1 -> v2: currently no structural change required (no-op),
    # but we must bump t
