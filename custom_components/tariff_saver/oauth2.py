"""OAuth2 helpers for Tariff Saver (myEKZ Keycloak).

This file forces Home Assistant to use the HA external URL callback:

    <external_url>/auth/external/callback

Instead of the My Home Assistant redirect proxy:

    https://my.home-assistant.io/redirect/oauth

Why:
- EKZ/Keycloak validates redirect_uri strictly.
- EKZ has whitelisted your Nabu Casa domain (or wildcard), not my.home-assistant.io.

Requirements:
- manifest.json includes:
    "oauth2": true,
    "application_credentials": true,
    "dependencies": ["application_credentials", "auth"]
- application_credentials.py exists (domain matches this integration)
"""
from __future__ import annotations

from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN

# Keycloak endpoints (from EKZ)
AUTHORIZATION_URL = "https://login.ekz.ch/auth/realms/myEKZ/protocol/openid-connect/auth"
TOKEN_URL = "https://login.ekz.ch/auth/realms/myEKZ/protocol/openid-connect/token"


class OAuth2FlowHandler(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle the OAuth2 flow for myEKZ."""

    DOMAIN = DOMAIN

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        # request refresh token (offline_access) + OIDC (openid)
        return {"scope": "openid offline_access"}


async def async_get_auth_implementation(hass):
    """Return auth implementation for the config_entry_oauth2_flow helpers."""
    # Force redirect_uri to HA external URL callback (Nabu Casa)
    redirect_uri = (hass.config.external_url or "").rstrip("/") + "/auth/external/callback"

    return config_entry_oauth2_flow.LocalOAuth2Implementation(
        hass,
        DOMAIN,
        client_id=None,  # provided by Application Credentials
        client_secret=None,  # provided by Application Credentials
        authorize_url=AUTHORIZATION_URL,
        token_url=TOKEN_URL,
        redirect_uri=redirect_uri,
    )
