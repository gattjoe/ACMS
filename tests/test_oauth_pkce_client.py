import asyncio
from fastmcp import Client

import httpx


async def test_unauthenticated_request(mcp_server_url: str):
    """
    Test making an unauthenticated request to the ACMS server.
    """
    print("=" * 50)
    print("Testing Unauthenticated Request")
    print("=" * 50)

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
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


async def main():
    """Main function."""

    mcp_server_url = "http://localhost:8765/mcp"

    try:
        # Test 1: Unauthenticated request (should fail if OAuth is enabled)
        await test_unauthenticated_request(mcp_server_url)

        async with Client("http://localhost:8765/mcp", auth="oauth") as client:
            result = await client.call_tool("acms_system_status")
            print(result)

    except Exception as e:
        print("=" * 50)
        print("Test Failed")
        print(f"Error: {e}")
        print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
