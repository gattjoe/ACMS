"""
OAuth 2.1 authentication module for a MCP server protected with Microsoft Entra ID.
Implements TokenVerifier protocol from MCP Python SDK.
"""

import logging
import os
from typing import Optional

import jwt
from dotenv import load_dotenv
from fastmcp.server.auth import AuthProvider
from jwt.jwks_client import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier

# Load environment variables
load_dotenv()

logger = logging.getLogger("ACMS.Auth")


class EntraTokenVerifier(TokenVerifier):
    """
    JWT token verifier for Microsoft Entra ID
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        required_scopes: Optional[list[str]] = None,
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.required_scopes = required_scopes or []

        # Microsoft Entra ID endpoints
        self.issuer_v2 = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.issuer_v1 = f"https://sts.windows.net/{tenant_id}/"
        self.jwks_uri = (
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        )

        # Initialize JWKS client for JWT signature verification
        self.jwks_client = PyJWKClient(self.jwks_uri, cache_keys=True)

        logger.info(f"EntraTokenVerifier initialized for tenant {tenant_id}")
        logger.info(f"JWKS URI: {self.jwks_uri}")
        logger.info(f"Required scopes: {self.required_scopes}")

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        """
        Verify a JWT bearer token from Microsoft Entra ID.

        Args:
            token: JWT token string

        Returns:
            AccessToken if valid, None if invalid
        """
        try:
            # Get signing key from JWKS
            try:
                signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            except Exception as e:
                logger.error(f"Failed to get signing key: {e}")
                return None

            # Decode and validate JWT (without issuer check first)
            try:
                payload = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],
                    audience=self.client_id,
                    options={
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_aud": True,
                        "verify_iss": False,  # We'll check issuer manually to support both v1 and v2
                    },
                )
            except jwt.ExpiredSignatureError:
                logger.warning("Token has expired")
                return None
            except jwt.InvalidAudienceError:
                logger.warning(f"Invalid audience. Expected: {self.client_id}")
                return None
            except jwt.InvalidTokenError as e:
                logger.warning(f"Invalid token: {e}")
                return None

            # Manually validate issuer (support both v1 and v2)
            token_issuer = payload.get("iss", "")
            if token_issuer not in [self.issuer_v1, self.issuer_v2]:
                logger.warning(
                    f"Invalid issuer. Got: {token_issuer}, Expected: {self.issuer_v1} or {self.issuer_v2}"
                )
                return None

            # Extract claims
            client_id_claim = payload.get(
                "aud", payload.get("azp", payload.get("appid", ""))
            )
            scopes_claim = payload.get("scp", payload.get("scope", ""))

            # Parse scopes (can be space-separated string or list)
            if isinstance(scopes_claim, str):
                scopes = scopes_claim.split() if scopes_claim else []
            elif isinstance(scopes_claim, list):
                scopes = scopes_claim
            else:
                scopes = []

            # Verify required scopes
            if self.required_scopes:
                missing_scopes = set(self.required_scopes) - set(scopes)
                if missing_scopes:
                    logger.warning(f"Missing required scopes: {missing_scopes}")
                    logger.warning(f"Token scopes: {scopes}")
                    return None

            # Extract expiration
            exp = payload.get("exp")

            # Create AccessToken
            access_token = AccessToken(
                token=token,
                client_id=client_id_claim,
                scopes=scopes,
                expires_at=exp,
                resource=self.client_id,
            )

            logger.info(f"Token verified successfully for client: {client_id_claim}")
            logger.debug(f"Token scopes: {scopes}")

            return access_token

        except Exception as e:
            logger.error(f"Unexpected error verifying token: {e}", exc_info=True)
            return None

    @classmethod
    def from_env(
        cls, required_scopes: Optional[list[str]] = None
    ) -> "EntraTokenVerifier":
        """
        Create an EntraTokenVerifier from environment variables.

        Environment variables required:
        - ENTRA_TENANT_ID: Microsoft Entra tenant ID
        - ENTRA_CLIENT_ID: Application (client) ID

        Args:
            required_scopes: List of required scopes for authentication

        Returns:
            EntraTokenVerifier instance

        Raises:
            ValueError: If required environment variables are missing
        """
        tenant_id = os.getenv("ENTRA_TENANT_ID")
        client_id = os.getenv("ENTRA_CLIENT_ID")

        if not tenant_id:
            raise ValueError("ENTRA_TENANT_ID environment variable is required")
        if not client_id:
            raise ValueError("ENTRA_CLIENT_ID environment variable is required")

        logger.info("Creating EntraTokenVerifier from environment variables")
        return cls(
            tenant_id=tenant_id, client_id=client_id, required_scopes=required_scopes
        )


class EntraAuthProvider(AuthProvider):
    """
    FastMCP AuthProvider for Microsoft Entra ID.
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        base_url: Optional[str] = None,
        required_scopes: Optional[list[str]] = None,
    ):
        super().__init__(base_url=base_url, required_scopes=required_scopes)

        self.tenant_id = tenant_id
        self.client_id = client_id

        # Create the underlying token verifier
        self._verifier = EntraTokenVerifier(
            tenant_id=tenant_id, client_id=client_id, required_scopes=required_scopes
        )

        logger.info(f"EntraAuthProvider initialized for tenant {tenant_id}")

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        """
        Verify a JWT bearer token from Microsoft Entra ID.

        This method delegates to the EntraTokenVerifier to perform
        the actual token validation using the MCP SDK's TokenVerifier protocol.

        Args:
            token: JWT token string

        Returns:
            AccessToken if valid, None if invalid
        """
        return await self._verifier.verify_token(token)

    @classmethod
    def from_env(
        cls, base_url: Optional[str] = None, required_scopes: Optional[list[str]] = None
    ) -> "EntraAuthProvider":
        """
        Create an EntraAuthProvider from environment variables.

        Environment variables required:
        - ENTRA_TENANT_ID: Microsoft Entra tenant ID
        - ENTRA_CLIENT_ID: Application (client) ID

        Args:
            base_url: Base URL of the MCP server
            required_scopes: List of required scopes for authentication

        Returns:
            EntraAuthProvider instance

        Raises:
            ValueError: If required environment variables are missing
        """
        tenant_id = os.getenv("ENTRA_TENANT_ID")
        client_id = os.getenv("ENTRA_CLIENT_ID")

        if not tenant_id:
            raise ValueError("ENTRA_TENANT_ID environment variable is required")
        if not client_id:
            raise ValueError("ENTRA_CLIENT_ID environment variable is required")

        logger.info("Creating EntraAuthProvider from environment variables")
        return cls(
            tenant_id=tenant_id,
            client_id=client_id,
            base_url=base_url,
            required_scopes=required_scopes,
        )
