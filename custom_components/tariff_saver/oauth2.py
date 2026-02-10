"""OAuth2 helpers for Tariff Saver (myEKZ Keycloak).

Problem observed:
- Home Assistant generated authorize URL with:
    redirect_uri=https://my.home-assistant.io/redirect/oauth
  which EKZ/Keycloak rejects unless EKZ whitelists that domain.

Fix:
- Force the redirect URI used in the *authorize URL* to be the HA external URL
  callback:
    <external_url>/auth/external/callback

How:
- Override AbstractOAuth2FlowHandler.async_get_redirect_uri()
- Also set the same redirect_uri in LocalOAuth2Implementation

Requirements:
- Settings → System → Network → External URL must be set (Nabu Casa URL).
- manifest.json includes:
    "oauth2": true,
    "application_credentials": true,
    "dependencies": ["application_credentials", "auth"]
- application_credentials.py exists and returns AuthorizationServer + ClientCredential.
"""
from __future__ import annotations

from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

# Keycloak endpoints (from EKZ)
AUTHORIZATION_URL = "https://login.ekz.ch/auth/realms/myEKZ/protocol/openid-connect/auth"
TOKEN_URL = "https://login.ekz.ch/auth/realms/myEKZ/protocol/openid-connect/token"


def _external_callback(hass) -> str:
    external = hass.config.external_url
    if not external:
        raise HomeAssistantError(
            "External URL is not set. Please set it to your Nabu Casa URL under "
            "Settings → System → Network → External URL."
        )
    return external.rstrip("/") + "/auth/external/callback"


class OAuth2FlowHandler(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle the OAuth2 flow for myEKZ."""

    DOMAIN = DOMAIN

    async def async_get_redirect_uri(self) -> str:
        """Return redirect_uri for the authorize URL.

        We MUST avoid the my.home-assistant.io redirect proxy, because EKZ only
        whitelists the EMS/HA URLs.
        """
        return _external_callback(self.hass)

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        # request refresh token (offline_access) + OIDC (openid)
        return {"scope": "openid offline_access"}


async def async_get_auth_implementation(hass):
    """Return auth implementation for the config_entry_oauth2_flow helpers."""
    redirect_uri = _external_callback(hass)

    return config_entry_oauth2_flow.LocalOAuth2Implementation(
        hass,
        DOMAIN,
        client_id=None,  # provided by Application Credentials
        client_secret=None,  # provided by Application Credentials
        authorize_url=AUTHORIZATION_URL,
        token_url=TOKEN_URL,
        redirect_uri=redirect_uri,
    )
