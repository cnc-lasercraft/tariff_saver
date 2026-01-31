"""Config flow for Tariff Saver (EKZ / myEKZ OAuth2)."""
from __future__ import annotations

import logging

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN as TS_DOMAIN

_LOGGER = logging.getLogger(__name__)


class TariffSaverConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=TS_DOMAIN):
    """Handle a config flow for Tariff Saver using OAuth2."""

    DOMAIN = TS_DOMAIN
    VERSION = 3

    @property
    def logger(self) -> logging.Logger:
        """Return logger for the OAuth2 flow handler."""
        return _LOGGER

    async def async_step_user(self, user_input=None):
        """Start OAuth2 flow."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        return await super().async_step_user(user_input)

    async def async_oauth_create_entry(self, data: dict) -> config_entries.ConfigEntry:
        """Create the config entry after OAuth2 is complete."""
        return self.async_create_entry(title="Tariff Saver (myEKZ)", data=data)

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
