"""
Network debugging script to test basic connectivity to the MCP server.
"""

import json
import socket
import sys
import time
from typing import Optional
import urllib.request
import urllib.error
import logging
import argparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mcp-server-network-debug")

def test_dns_resolution(hostname: str) -> Optional[str]:

    try:
        logger.info(f"🔍 Testing DNS resolution for: {hostname}")
        ip_address: str = socket.gethostbyname(hostname)
        logger.info(f"✅ DNS resolution successful: {hostname} -> {ip_address}")
        return ip_address
    except socket.gaierror as e:
        logger.error(f"❌ DNS resolution failed: {e}")
        return None

def test_tcp_connection(host: str, port: int, request_timeout: float = 3.0) -> bool:

    try:
        logger.info(f"🔌 Testing TCP connection to {host}:{port}")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, proto=0)
        sock.settimeout(request_timeout)

        start_time: float = time.time()
        result: int = sock.connect_ex((host, port))
        duration: float = time.time() - start_time

        sock.close()

        if result == 0:
            logger.info(f"✅ TCP connection successful in {duration:.3f}s")
            return True
        else:
            logger.error(f"❌ TCP connection failed: error code {result}")
            return False

    except Exception as e:
        logger.error(f"❌ TCP connection exception: {e}")
        return False

def test_mcp_initialize(base_url: str) -> bool:

    url = base_url + "/mcp"

    mcpInitializationData = {
        "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},"clientInfo": {"name": "test-mcp", "title": "test-mcp", "version": "0.1"}}, "jsonrpc": "2.0", "id": "0"}

    json_string = json.dumps(mcpInitializationData)
    encoded_data = json_string.encode('utf-8')

    try:
        logger.info(f"Testing MCP initialization: {url}")

        start_time = time.time()
        request = urllib.request.Request(url)
        request.add_header('Content-Type', 'application/json', )
        request.add_header('Accept', 'application/json, text/event-stream')
        request.add_header('Connection', 'keep-alive')
        with urllib.request.urlopen(request, data=encoded_data) as response:
            duration = time.time() - start_time
            status_code = response.getcode()
            headers = dict(response.getheaders())

            logger.info(f"MCP request successful in {duration:.3f}s")
            logger.info(f"HTTP Status code: {status_code}")
            logger.info(f"Response headers: {headers}")

        return True

    except urllib.error.HTTPError as e:
        logger.error(f"❌ HTTP error: {e.code} - {e.reason}")
        return False
    except urllib.error.URLError as e:
        logger.error(f"❌ URL error: {e.reason}")
        return False
    except socket.timeout as e:
        logger.error(f"❌ HTTP timeout: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ HTTP request exception: {e}")
        return False

def test_mcp_ping(base_url: str) -> bool:

    url = base_url + "/mcp"

    mcpPingData = {
        "method": "ping", "jsonrpc": "2.0", "id": "123"
    }

    json_string = json.dumps(mcpPingData)
    encoded_data = json_string.encode('utf-8')
    try:
        logger.info(f"Testing MCP ping request to: {url}")

        start_time = time.time()
        request = urllib.request.Request(url)
        request.add_header('Content-Type', 'application/json', )
        request.add_header('Accept', 'application/json, text/event-stream')
        request.add_header('Connection', 'keep-alive')
        with urllib.request.urlopen(request, data=encoded_data) as response:
            duration = time.time() - start_time
            status_code = response.getcode()
            headers = dict(response.getheaders())

            logger.info(f"MCP ping request successful in {duration:.3f}s")
            logger.info(f"HTTP Status code: {status_code}")
            logger.info(f"Response headers: {headers}")

        return True

    except urllib.error.HTTPError as e:
        logger.error(f"❌ HTTP error: {e.code} - {e.reason}")
        return False
    except urllib.error.URLError as e:
        logger.error(f"❌ URL error: {e.reason}")
        return False
    except socket.timeout as e:
        logger.error(f"❌ HTTP timeout: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ HTTP request exception: {e}")
        return False

def parse_arguments() -> argparse.Namespace:

    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Network debugging script to test MCP server connectivity",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        )

    parser.add_argument(
        'url',
        default="http://localhost:8765",
        help='Full URL (e.g., http://server:port)'
    )

    return parser.parse_args()

def main() -> int:
    """Run comprehensive network debugging tests.

    Returns:
        Exit code: 0 for success, 1 for failure
    """
    args: argparse.Namespace = parse_arguments()

    target_url = args.url

    logger.info("NETWORK DEBUGGING TESTS")
    logger.info(f"Target: {target_url}")

    # Parse URL
    hostname: str
    port: int
    if target_url.startswith("http://"):
        hostname = target_url[7:].split(":")[0]
        port = int(target_url.split(":")[-1]) if ":" in target_url[7:] else 80
    elif target_url.startswith("https://"):
        hostname = target_url[8:].split(":")[0]
        port = int(target_url.split(":")[-1]) if ":" in target_url[8:] else 443
    else:
        logger.error("❌ Invalid URL format. Use http:// or https://")
        return 1

    logger.info(f"Hostname: {hostname}")
    logger.info(f"Port: {port}")

    # Test 1: DNS Resolution
    ip_address = test_dns_resolution(hostname)
    if not ip_address:
        logger.error("🚨 DNS resolution failed - cannot proceed")
        return 1

    # Test 2: TCP Connection
    if not test_tcp_connection(ip_address, port):
        logger.error("🚨 TCP connection failed - server may be down")
        return 1

    # Test 3: MCP initialization
    if not test_mcp_initialize(target_url):
        logger.error("🚨 HTTP request failed - server may not be responding")
        return 1

    # Test 4: MCP ping
    #if not test_mcp_ping(target_url):
    #    logger.error("🚨 MCP ping - server may not be responding")
    #    return 1

    logger.info("=" * 20)
    logger.info("ALL TESTS PASSED!")
    logger.info("=" * 20)

    return 0

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
