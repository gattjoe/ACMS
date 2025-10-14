"""
Test script for ACMS OAuth 2.1 authentication.

This script demonstrates how to:
1. Obtain an access token from Microsoft Entra ID
2. Use the token to make authenticated requests to the ACMS server
"""

import asyncio
import json
import os
import sys
from typing import Optional

import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def parse_sse_response(sse_text: str) -> dict:
    """
    Parse Server-Sent Events (SSE) response to extract JSON data.

    Args:
        sse_text: SSE formatted text (e.g., "event: message\ndata: {...}")

    Returns:
        dict: Parsed JSON data from the SSE message
    """
    lines = sse_text.strip().split("\n")
    for line in lines:
        if line.startswith("data: "):
            json_str = line[6:]  # Remove 'data: ' prefix
            return json.loads(json_str)
    return {}


class EntraOAuthClient:
    """
    OAuth 2.1 client for Microsoft Entra ID.
    Handles token acquisition using client credentials flow.
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        """
        Initialize the OAuth client.
        """
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_endpoint = (
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        )

    async def get_access_token(self, scope: Optional[str] = None) -> dict:
        """
        Obtain an access token using client credentials flow.

        Args:
            scope: OAuth scope to request (default: {client_id}/.default)

        Returns:
            dict: Token response containing access_token, token_type, expires_in, etc.
        """
        # Default scope is the application ID URI with /.default
        if not scope:
            scope = f"{self.client_id}/.default"

        print("=" * 50)
        print("Testing obtaining access token from Entra ID")
        print("=" * 50)
        print(f"Token Endpoint: {self.token_endpoint}")
        print(f"Client ID: {self.client_id}")
        print(f"Scope: {scope}")

        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": scope,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.token_endpoint,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

                response.raise_for_status()
                token_data = response.json()

                print("Access token obtained successfully!")
                print(f"Token Type: {token_data.get('token_type')}")
                print(f"Expires In: {token_data.get('expires_in')} seconds")

                return token_data

            except httpx.HTTPError as e:
                print(f"Failed to obtain access token: {e}")
                if hasattr(e, "response") and e.response is not None:
                    print(f"Response Status: {e.response.status_code}")
                    print(f"Response Body: {e.response.text}")
                raise


async def test_unauthenticated_request(server_url: str):
    """
    Test making an unauthenticated request to the ACMS server.
    """
    print("=" * 50)
    print("Testing Unauthenticated Request")
    print(f"Server URL: {server_url}")
    print("=" * 50)

    headers = {
        "Accept": "application/json, text/event-stream",
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(f"{server_url}/mcp", headers=headers)

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
    server_url: str, access_token: str, endpoint: str = "/mcp"
):
    """
    Test making an authenticated request to the ACMS server using MCP protocol.

    Args:
        server_url: Base URL of the ACMS server
        access_token: OAuth access token
        endpoint: API endpoint to test (default: /mcp)
    """
    print("=" * 50)
    print("Testing Authenticated Request with MCP Protocol")
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
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    }

    # Use persistent client to maintain session cookies
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            # Step 1: Initialize MCP session
            response = await client.post(
                f"{server_url}{endpoint}", headers=headers, json=mcp_init_request
            )

            if response.status_code == 200:
                print("OAuth authentication SUCCESSFUL!")
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

                    print("Full authentication flow completed successfully!")

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
                        f"{server_url}{endpoint}",
                        headers=headers,
                        json=initialized_notification,
                    )

                    # Step 3: List tools
                    print("=" * 50)
                    print("Listing Available Tools")
                    print("=" * 50)

                    list_tools_request = {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/list",
                        "params": None,  # Explicitly set to null for tools/list
                    }

                    tools_response = await client.post(
                        f"{server_url}{endpoint}",
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
                                f"COMPLETE SUCCESS! OAuth 2.1 authentication with MCP is fully operational!"
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


async def test_list_tools(server_url: str, access_token: str):
    """
    List all available tools from the ACMS server.

    Args:
        server_url: Base URL of the ACMS server
        access_token: OAuth access token
    """
    print("=" * 50)
    print("Listing Available Tools")
    print("=" * 50)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    # MCP tools/list request
    list_tools_request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.post(
                f"{server_url}/mcp", headers=headers, json=list_tools_request
            )

            if response.status_code == 200:
                print("Successfully retrieved tools list!")

                # Parse SSE response
                data = parse_sse_response(response.text)
                result = data.get("result", {})
                tools = result.get("tools", [])

                if tools:
                    print(f"\n📋 Found {len(tools)} tools:\n")
                    for i, tool in enumerate(tools[:10], 1):  # Show first 10
                        print(f"{i}. {tool.get('name', 'Unknown')}")
                        desc = tool.get("description", "No description")
                        print(f"   {desc[:80]}{'...' if len(desc) > 80 else ''}")

                    if len(tools) > 10:
                        print(f"\n   ... and {len(tools) - 10} more tools")
                else:
                    print("No tools found")

                return True

            else:
                print(f"⚠ Failed to list tools: {response.status_code}")
                print(f"Response: {response.text[:500]}")
                return False

        except Exception as e:
            print(f"✗ Request failed: {e}")
            return False


async def test_container_list(server_url: str, access_token: str):
    """
    Test calling a specific ACMS tool with authentication.

    Args:
        server_url: Base URL of the ACMS server
        access_token: OAuth access token
    """
    print("\n" + "=" * 50)
    print("Testing ACMS Tool: container_list")
    print("=" * 50)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    # MCP tool call format
    payload = {
        "method": "tools/call",
        "params": {"name": "container_list", "arguments": {"all": True}},
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"{server_url}/mcp/v1/tools/call", headers=headers, json=payload
            )

            print(f"Response Status: {response.status_code}")

            if response.status_code == 200:
                print("✓ Tool call succeeded!")
                try:
                    data = response.json()
                    print(f"Response: {data}")
                except Exception:
                    print(f"Response body: {response.text[:500]}")
            else:
                print("Tool call failed")
                print(f"Response: {response.text}")

        except httpx.HTTPError as e:
            print(f"✗ Request failed: {e}")


async def main():
    """Main test function."""
    print("=" * 50)
    print("ACMS OAuth 2.1 Authentication Test")
    print("=" * 50)

    # Get configuration from environment
    tenant_id = os.getenv("ENTRA_TENANT_ID")
    client_id = os.getenv("ENTRA_CLIENT_ID")
    client_secret = os.getenv("ENTRA_CLIENT_SECRET")
    server_url = os.getenv("ACMS_SERVER_URL", "http://localhost:8765")

    # Validate configuration
    if not all([tenant_id, client_id, client_secret]):
        print("Error: Missing required environment variables")
        print("Required: ENTRA_TENANT_ID, ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET")
        print("Please check your .env file")
        sys.exit(1)

    print("Configuration:")
    print(f"  Tenant ID: {tenant_id}")
    print(f"  Client ID: {client_id}")
    print(f"  Server URL: {server_url}")

    # Create OAuth client
    oauth_client = EntraOAuthClient(tenant_id, client_id, client_secret)

    try:
        # Test 1: Unauthenticated request (should fail if OAuth is enabled)
        await test_unauthenticated_request(server_url)

        # Test 2: Obtain access token
        token_response = await oauth_client.get_access_token()
        access_token = token_response["access_token"]

        # Test 3: Authenticated request to the MCP endpoint using proper MCP protocol
        # This also lists tools in the same session
        await test_authenticated_request(server_url, access_token, "/mcp")

        # Test 4: Call a specific tool (optional, may fail if container system not started)
        # await test_container_list(server_url, access_token)

    except Exception as e:
        print("=" * 50)
        print("Test Failed")
        print(f"Error: {e}")
        print("=" * 50)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
