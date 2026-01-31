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

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor"]

CONFIG_VERSION = 2
CONFIG_MINOR_VERSION = 0


def _parse_hhmm(value: str) -> tuple[int, int]:
    """Parse 'HH:MM' -> (hour, minute). Fallback to DEFAULT_PUBLISH_TIME on bad input."""
    try:
        hh, mm = value.strip().split(":")
        h = int(hh)
        m = int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass

    try:
        hh, mm = DEFAULT_PUBLISH_TIME.strip().split(":")
        return int(hh), int(mm)
    except Exception:
        return 18, 15


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up domain (YAML not used)."""
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries to the latest version."""
    _LOGGER.debug(
        "Migrating %s entry %s from version %s.%s",
        DOMAIN,
        entry.entry_id,
        entry.version,
        entry.minor_version,
    )

    data = dict(entry.data)
    options = dict(entry.options)

    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            version=CONFIG_VERSION,
            minor_version=CONFIG_MINOR_VERSION,
        )
        _LOGGER.info("Migrated %s entry %s to version %s.%s", DOMAIN, entry.entry_id, CONFIG_VERSION, CONFIG_MINOR_VERSION)
        return True

    if entry.version == CONFIG_VERSION:
        return True

    if entry.version > CONFIG_VERSION:
        _LOGGER.error(
            "Cannot migrate %s entry %s from future version %s (supports up to %s)",
            DOMAIN,
            entry.entry_id,
            entry.version,
            CONFIG_VERSION,
        )
        return False

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=CONFIG_VERSION,
        minor_version=CONFIG_MINOR_VERSION,
    )
    _LOGGER.warning(
        "Unexpected old %s entry version %s for %s; force-bumped to %s.%s",
        DOMAIN,
        entry.version,
        entry.entry_id,
        CONFIG_VERSION,
        CONFIG_MINOR_VERSION,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tariff Saver from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    api = EkzTariffApi(session)

    coordinator = TariffSaverCoordinator(hass, api, config=dict(entry.data))
    hass.data[DOMAIN][entry.entry_id] = coordinator

    publish_time = entry.data.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME)
    hour, minute = _parse_hhmm(publish_time)

    async def _daily_refresh(now) -> None:  # noqa: ANN001
        await coordinator.async_request_refresh()

    unsub_key = f"{entry.entry_id}_unsub_daily"
    hass.data[DOMAIN][unsub_key] = async_track_time_change(
        hass, _daily_refresh, hour=hour, minute=minute, second=0
    )

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Tariff Saver config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    unsub_key = f"{entry.entry_id}_unsub_daily"
    unsub = hass.data.get(DOMAIN, {}).pop(unsub_key, None)
    if unsub:
        unsub()

    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)

    return unload_ok
