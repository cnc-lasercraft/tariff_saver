"""Application Credentials platform for Tariff Saver (OAuth2)."""
from __future__ import annotations

from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return the OAuth2 authorization server configuration.

    NOTE: We will fill the exact EKZ/Keycloak endpoints in the next step
    once we copy them from the EKZ documentation (authorize + token URLs).
    """
    return AuthorizationServer(
        authorize_url="TODO_AUTHORIZE_URL",
        token_url="TODO_TOKEN_URL",
    )
