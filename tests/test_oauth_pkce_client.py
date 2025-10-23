import asyncio
import base64
import hashlib
import json
import os
import secrets
import sys
import webbrowser
from aiohttp import web
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def parse_sse_response(sse_text: str) -> dict:
    """
    Parse Server-Sent Events (SSE) response to extract JSON data.
    """
    lines = sse_text.strip().split("\n")
    for line in lines:
        if line.startswith("data: "):
            json_str = line[6:]  # Remove 'data: ' prefix
            return json.loads(json_str)
    return {}


class EntraOAuthPKCEFlow:
    """
    OAuth 2.1 client for Microsoft Entra ID with PKCE support.
    Handles token acquisition using authorization code flow with PKCE.
    """

    # Constants
    CALLBACK_TIMEOUT = 300
    MAX_RETRY_ATTEMPTS = 3

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        redirect_uri: str = "http://localhost:8080",
        mcp_server_url: str = "http://localhost:8765",
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.authorization_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"  # Needs to be generic
        self.token_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"  # Needs to be generic

        self.scope = f"{self.client_id}/.default"
        # self.resource = f"{mcp_server_url}/"

        # PKCE parameters
        self.code_verifier: Optional[str] = None
        self.code_challenge: Optional[str] = None
        self.state: Optional[str] = None

        # Track retry attempts for silent sign-in failures
        self.retry_count: int = 0

    def generate_pkce_pair(self) -> None:
        """
        Generate PKCE code verifier and challenge.
        """
        random_bytes = secrets.token_bytes(32)

        # Generate code verifier
        self.code_verifier = (
            base64.urlsafe_b64encode(random_bytes).decode("utf-8").rstrip("=")
        )

        # Generate code challenge (SHA256 has of verifier)
        challenge_bytes = hashlib.sha256(self.code_verifier.encode("utf-8")).digest()
        self.code_challenge = (
            base64.urlsafe_b64encode(challenge_bytes).decode("utf-8").rstrip("=")
        )

        # Generate state for CSRF protection
        self.state = (
            base64.urlsafe_b64encode(secrets.token_bytes(32))
            .decode("utf-8")
            .rstrip("=")
        )

    def get_authorization_url(self) -> str:
        """
        Generate the authorization URL with PKCE parameters.

        Returns:
            str: Authorization URL with PKCE challenge
        """
        # Generate PKCE parameters
        self.generate_pkce_pair()

        # Build authorization URL with PKCE parameters
        params = {
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "scope": self.scope,
            "state": self.state,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
            #    "resource": self.resource,
        }

        auth_url = f"{self.authorization_endpoint}?{urlencode(params)}"

        print("=" * 50)
        print("PKCE Authorization Flow")
        print("=" * 50)
        print(f"Authorization Endpoint: {self.authorization_endpoint}")
        print(f"Params: {params}")

        return auth_url

    async def start_callback_server(self, port: int = 8080) -> Tuple[str, str]:
        """
        Start a local HTTP server to receive the authorization code.

        Args:
            port: Port to listen on (must match redirect_uri)

        Returns:
            Tuple of (authorization_code, state)
        """
        auth_code_future: asyncio.Future = asyncio.Future()

        async def handle_callback(request: web.Request) -> web.Response:
            """Handle the OAuth callback from the browser."""
            query = parse_qs(urlparse(str(request.url)).query)

            if "error" in query:
                error = query["error"][0]
                error_desc = query.get("error_description", ["Unknown error"])[0]
                auth_code_future.set_exception(
                    Exception(f"Oauth error: {error} - {error_desc}")
                )
                return web.Response(
                    text=self._error_html(error, error_desc), content_type="text/html"
                )

            # Extract authorization code and state
            if "code" in query and "state" in query:
                code = query["code"][0]
                state = query["state"][0]
                auth_code_future.set_result((code, state))
                return web.Response(text=self._success_html(), content_type="text/html")

            # No code received
            auth_code_future.set_exception(Exception("No authorization code received"))
            return web.Response(
                text="Authentication failed - no code received!", status=400
            )

        app = web.Application()
        app.router.add_get("/", handle_callback)

        parsed = urlparse(self.redirect_uri)
        port = parsed.port or 8080

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            code, state = await asyncio.wait_for(
                auth_code_future, timeout=self.CALLBACK_TIMEOUT
            )
            return code, state
        except asyncio.TimeoutError:
            raise Exception("Authentication Timeout")
        finally:
            await runner.cleanup()

    @staticmethod
    def _success_html() -> str:
        """Generate success HTML page"""
        return """
            <html>
                <body style="font-family: Arial; text-align: center; padding: 50px;">
                    <h1>Authentication Successful!</h1>
                </body>
            </html>
        """

    @staticmethod
    def _error_html(error, error_desc) -> str:
        """Generate error HTML page"""
        return """
            <html>
                <body style="font-family: Arial; text-align: center; padding: 50px;">
                    <h1>Authentication Failed!</h1>
                    <h3>{error}</h3>
                    <h3>{error_desc}</h3>
                </body>
            </html>
        """

    async def exchange_code_for_token(self, auth_code: str) -> Dict[str, Any]:
        """
        Exchange authorization code for access token using PKCE verification.

        Args:
            authorization_code: The authorization code received from OAuth callback

        Returns:
            dict: Token response containing access_token, token_type, expires_in, etc.
        """
        if not self.code_verifier:
            raise ValueError(
                "Code verifier not found. Must call get_authorization_url() first."
            )

        print("=" * 50)
        print("Exchanging Authorization Code for Access Token")
        print("=" * 50)

        # Token request with PKCE verification
        token_data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "code": auth_code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": self.code_verifier,
        }

        print(f"Token data: {token_data}")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.token_endpoint,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                print(f"Token Exchange Failed (Status {response.status_code})")
                print(f"Response Headers: {dict(response.headers)}")

                response.raise_for_status()

            token_data = response.json()

            print("Access token obtained successfully!")
            print(f"Token Type: {token_data.get('token_type')}")
            print(f"Expires In: {token_data.get('expires_in')} seconds")
            if "scope" in token_data:
                print(f"Scopes: {token_data.get('scope')}")

            return token_data

    async def get_access_token(self) -> str:
        """
        Complete PKCE flow and return access token

        This is the main entry point that orchestrates the full PKCE flow:
        1. Generate authorization URL
        2. Open browser for user authentication
        3. Receives callback and captures authorization code
        4. Exchange code for access token

        Returns:
            dict: Token response with access_token
        """
        # Step 1: Generate authorization URL with PKCE
        auth_url = self.get_authorization_url()

        # Step 2: Open browser for user consent
        print("=" * 50)
        print("Opening browser for user authorization...")
        print("=" * 50)
        print(f"URL: {auth_url}...")

        webbrowser.open(auth_url)

        # Step 3: Start local server to receive authorization code
        port = int(urlparse(self.redirect_uri).port or 8080)
        auth_code, returned_state = await self.start_callback_server(port=port)

        if returned_state != self.state:
            raise Exception("State mismatch - possible CSRF attack")

        if not auth_code:
            raise Exception("Failed to receive authorization code")

        print(f"Authorization code received: {auth_code[:20]}...")

        # Step 4: Exchange code for token with PKCE verification
        token_data = await self.exchange_code_for_token(auth_code)

        return token_data["access_token"]


async def test_unauthenticated_request(mcp_server_url: str):
    """
    Test making an unauthenticated request to the ACMS server.
    """
    print("=" * 50)
    print("Testing Unauthenticated Request")
    print(f"Server URL: {mcp_server_url}")
    print("=" * 50)

    headers = {
        "Accept": "application/json, text/event-stream",
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(f"{mcp_server_url}/mcp", headers=headers)

            if response.status_code == 401:
                print("Server correctly rejected unauthenticated request (401)")
                print(f"Response Headers: {dict(response.headers)}")
                print(
                    f"WWW-Authenticate: {response.headers.get('WWW-Authenticate', 'Not present')}"
                )
            elif response.status_code == 200:
                print("Server accepted unauthenticated request (OAuth may be disabled)")
            else:
                print(f"Unexpected response: {response.status_code}")
                print(f"Response: {response.text}")

        except httpx.HTTPError as e:
            print(f"Request failed: {e}")


async def test_authenticated_request(
    mcp_server_url: str, access_token: str, endpoint: str = "/mcp"
):
    """
    Test making an authenticated request to the ACMS server using MCP protocol.

    Args:
        server_url: Base URL of the ACMS server
        access_token: OAuth access token (obtained via PKCE flow)
    """
    print("=" * 50)
    print("Testing Authenticated Request")
    print("=" * 50)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    mcp_init_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client-pkce", "version": "1.0.0"},
        },
    }

    # Use persistent client to maintain session cookies
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            # Step 1: Initialize MCP session
            response = await client.post(
                f"{mcp_server_url}{endpoint}", headers=headers, json=mcp_init_request
            )

            if response.status_code == 200:
                print("OAuth PKCE authentication SUCCESSFUL!")
                print(f"Response Status: {response.status_code}")

                try:
                    # Parse SSE response
                    data = parse_sse_response(response.text)

                    print("MCP Server Response:")
                    result = data.get("result", {})
                    print(f"  Protocol Version: {result.get('protocolVersion', 'N/A')}")
                    print(
                        f"  Server Name: {result.get('serverInfo', {}).get('name', 'N/A')}"
                    )

                    capabilities = result.get("capabilities", {})
                    if capabilities:
                        print(f"  Server Capabilities: {list(capabilities.keys())}")

                    print("Full PKCE authentication flow completed successfully!")

                    # Extract session ID from response headers
                    session_id = response.headers.get("mcp-session-id")
                    if session_id:
                        print(f"MCP Session ID: {session_id}")
                        # Add session ID to headers for subsequent requests
                        headers["mcp-session-id"] = session_id
                    else:
                        print("No session ID found in response headers")

                    # Step 2: Send initialized notification (required by MCP protocol)
                    initialized_notification = {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                    }

                    await client.post(
                        f"{mcp_server_url}{endpoint}",
                        headers=headers,
                        json=initialized_notification,
                    )

                    # Step 3: List tools
                    print("\n" + "=" * 70)
                    print("Listing Available Tools")
                    print("=" * 70)

                    list_tools_request = {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/list",
                        "params": None,  # Explicitly set to null for tools/list
                    }

                    tools_response = await client.post(
                        f"{mcp_server_url}{endpoint}",
                        headers=headers,
                        json=list_tools_request,
                    )

                    if tools_response.status_code == 200:
                        print("Successfully retrieved tools list!")

                        # Parse SSE response
                        tools_data = parse_sse_response(tools_response.text)
                        tools_result = tools_data.get("result", {})
                        tools = tools_result.get("tools", [])

                        if tools:
                            print(f"Found {len(tools)} tools:\n")
                            for i, tool in enumerate(tools[:3], 1):  # Show first 3
                                print(f"{i}. {tool.get('name', 'Unknown')}")
                                desc = tool.get("description", "No description")
                                print(
                                    f"   {desc[:80]}{'...' if len(desc) > 80 else ''}"
                                )

                            if len(tools) > 3:
                                print(f"\n   ... and {len(tools) - 3} more tools")

                            print(
                                "COMPLETE SUCCESS! OAuth 2.1 with PKCE authentication is fully operational!"
                            )
                        else:
                            print("No tools found in response")
                    else:
                        print(f"Failed to list tools: {tools_response.status_code}")
                        print(f"Response: {tools_response.text[:500]}")

                    return True  # Return success

                except Exception as e:
                    print(f"Error during session: {e}")
                    print(f"Response data: {response.text[:500]}")
                    return False

            elif response.status_code == 401:
                print("Authentication failed (401 Unauthorized)")
                print(f"Response: {response.text}")
                print(
                    f"WWW-Authenticate: {response.headers.get('WWW-Authenticate', 'Not present')}"
                )
            elif response.status_code == 403:
                print(
                    "Authorization failed (403 Forbidden) - Token may lack required scopes"
                )
                print(f"Response: {response.text}")
            else:
                print(f"Unexpected response: {response.status_code}")
                print(f"Response: {response.text}")
                print(
                    "\nNote: Non-200 responses may indicate protocol version mismatch, not auth failure"
                )

        except httpx.HTTPError as e:
            print(f"Request failed: {e}")


async def main():
    """Main function."""

    # Get configuration from environment
    tenant_id = os.getenv("ENTRA_TENANT_ID")
    client_id = os.getenv("ENTRA_CLIENT_ID")
    redirect_uri = os.getenv("ENTRA_REDIRECT_URI", "http://localhost:8080")
    mcp_server_url = os.getenv("ACMS_SERVER_URL", "http://localhost:8765")

    # Validate configuration
    if not all([tenant_id, client_id]):
        print("Error: Missing required environment variables")
        print("Required: ENTRA_TENANT_ID, ENTRA_CLIENT_ID")
        print("Please check your .env file")
        sys.exit(1)

    # Create OAuth PKCE client
    oauth_client = EntraOAuthPKCEFlow(tenant_id, client_id, redirect_uri)

    try:
        # Test 1: Unauthenticated request (should fail if OAuth is enabled)
        await test_unauthenticated_request(mcp_server_url)

        # Test 2: Complete PKCE flow to obtain access token
        # This will open a browser, get user consent, and exchange the code
        access_token = await oauth_client.get_access_token()

        # Test 3: Authenticated request to the MCP endpoint using proper MCP protocol
        # This also lists tools in the same session
        await test_authenticated_request(mcp_server_url, access_token, "/mcp")

    except Exception as e:
        print("=" * 70)
        print("Test Failed")
        print(f"Error: {e}")
        print("=" * 70)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
