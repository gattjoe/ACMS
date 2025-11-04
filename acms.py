#!/usr/bin/env python3
"""
This MCP server provides programmatic access to Apple's container CLI tool on macOS.
It wraps the `container` command-line tool and exposes it through the MCP protocol.

Usage:
  python3 acms.py --port 8765              # HTTP server on port 8765
  python3 acms.py --ssl --port 8443        # HTTPS server with SSL on port 8443
"""

from typing import TypeAlias, Optional, List, Dict, Any
import argparse
import logging
import asyncio
import shutil
import json
import sys
import os

from fastmcp.server.auth.providers.azure import AzureProvider
from dotenv import load_dotenv
import uvicorn
import fastmcp

# Load environment variables
load_dotenv()

CommandResult: TypeAlias = Dict[str, Any]

# Configure enhanced logging to stderr to avoid interfering with stdio communication
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - [PID:%(process)d] [%(funcName)s:%(lineno)d] - %(message)s",
    stream=sys.stderr,
    force=True,  # Ensure our config overrides any existing config
)
logger = logging.getLogger("ACMS")

# Set up more comprehensive logging for all relevant components - MAXIMUM VERBOSITY
logging.getLogger("uvicorn").setLevel(logging.DEBUG)
logging.getLogger("uvicorn.access").setLevel(logging.DEBUG)
logging.getLogger("uvicorn.error").setLevel(logging.DEBUG)
logging.getLogger("uvicorn.asgi").setLevel(logging.DEBUG)
logging.getLogger("uvicorn.protocols").setLevel(logging.DEBUG)
logging.getLogger("uvicorn.protocols.http").setLevel(logging.DEBUG)
logging.getLogger("fastmcp").setLevel(logging.DEBUG)
logging.getLogger("mcp").setLevel(logging.DEBUG)
logging.getLogger("starlette").setLevel(logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("httpcore").setLevel(logging.DEBUG)

# Force all loggers to use our stderr handler
for logger_name in [
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "uvicorn.asgi",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "fastmcp",
    "mcp",
    "starlette",
]:
    specific_logger = logging.getLogger(logger_name)
    specific_logger.handlers.clear()
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - [PID:%(process)d] - %(message)s"
        )
    )
    specific_logger.addHandler(stderr_handler)
    specific_logger.propagate = False


def _validate_container_arg(arg: str) -> str:
    """
    Validate container command arguments to prevent command injection.

    Args:
        arg: Command argument to validate

    Returns:
        str: Validated argument

    Raises:
        ValueError: If argument contains forbidden characters
    """
    if not isinstance(arg, str):
        raise ValueError(f"Argument must be a string, got {type(arg).__name__}")

    # Disallow dangerous characters that could be used for command injection
    forbidden_chars = [";", "|", "&", "$", "`", "\n", "\r", "\x00"]
    for char in forbidden_chars:
        if char in arg:
            raise ValueError(f"Invalid character '{repr(char)}' in argument. ")

    # Additional check for shell metacharacters
    if arg.strip().startswith("-") and any(c in arg for c in ["$(", "${", "`"]):
        raise ValueError(f"Argument appears to contain shell command substitution: {arg}. ")

    return arg


def check_container_available() -> bool:
    """
    Check if the container CLI tool is available.
    """
    available: bool = shutil.which("container") is not None
    logger.info(f"Container CLI availability check: {available}")

    return available


async def run_container_command(*args: str) -> CommandResult:
    """
    Execute a container command and return the result

    Args:
        *args: Command arguments to pass to container CLI

    Returns:
        Dict[str, Any]: Command execution result containing stdout, stderr, return_code, and command

    Raises:
        RuntimeError: If command execution fails
        ValueError: If arguments contain forbidden characters
    """
    # Validate all arguments to prevent command injection
    try:
        validated_args = [_validate_container_arg(arg) for arg in args]
    except ValueError as e:
        logger.error(f"[Argument validation failed: {e}")
        raise ValueError(f"Invalid command argument: {e}")

    cmd = ["container"] + validated_args
    logger.info(f"Executing: {' '.join(cmd)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

        result = {
            "stdout": stdout_text,
            "stderr": stderr_text,
            "return_code": process.returncode,
            "command": " ".join(cmd),
        }

        # Log command completion with detailed results
        if process.returncode == 0:
            logger.info("Completed successfully (exit code: 0)")
        else:
            logger.warning(f"[Failed with exit code: {process.returncode}")
            if stderr_text:
                logger.warning(f"Error output: {stderr_text.strip()}")

        return result

    except Exception as e:
        error_msg = f"Exception executing command '{' '.join(cmd)}': {e}"
        logger.error(f"{error_msg}")
        logger.error("Stack trace:", exc_info=True)

        raise RuntimeError(f"Failed to execute command: {e}")


def validate_array_parameter(param: Any, param_name: str) -> Optional[List[str]]:
    """
    Validate and normalize array parameters that may come as JSON strings or actual lists.

    Args:
        param: The parameter to validate (could be None, str, or List[str])
        param_name: Name of the parameter for error messages

    Returns:
        Optional[List[str]]: Validated list or None

    Raises:
        ValueError: If parameter is invalid
    """
    if param is None:
        return None

    if isinstance(param, list):
        # Already a list, validate all elements are strings
        if all(isinstance(item, str) for item in param):
            if len(param) == 0:
                raise ValueError(
                    f"Parameter '{param_name}' cannot be an empty array. Either omit the parameter or provide at least one item."
                )
            return param
        else:
            raise ValueError(
                f"Parameter '{param_name}' must be a list of strings, but contains non-string elements: {[type(item).__name__ for item in param if not isinstance(item, str)]}"
            )

    if isinstance(param, str):
        # Try to parse as JSON array
        try:
            parsed = json.loads(param)
            if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
                if len(parsed) == 0:
                    raise ValueError(
                        f"Parameter '{param_name}' cannot be an empty array. Either omit the parameter or provide at least one item."
                    )
                return parsed
            else:
                raise ValueError(
                    f"Parameter '{param_name}' JSON must be an array of strings, but got: {type(parsed).__name__} with elements: {[type(item).__name__ for item in parsed] if isinstance(parsed, list) else 'N/A'}"
                )
        except json.JSONDecodeError:
            # Not JSON, treat as single string and convert to list
            return [param]

    raise ValueError(
        f"Parameter '{param_name}' must be a string, array of strings, or null, but got: {type(param).__name__}"
    )


def format_command_result(result: CommandResult) -> str:
    """
    Format the result from a container command execution with enhanced logging.

    Args:
        result: Dictionary containing command execution results

    Returns:
        str: Formatted result string for client response
    """
    try:
        duration = result.get("duration", 0)

        # Format response based on return code
        if result["return_code"] == 0:
            response = f"Command executed successfully:\\n{result['command']}\\n"
            if duration > 0:
                response += f"Duration: {duration:.3f}s\\n\\n"
            else:
                response += "\\n"

            if result["stdout"]:
                response += f"Output:\\n{result['stdout']}"
            if result["stderr"]:
                response += f"\\nWarnings/Info:\\n{result['stderr']}"
        else:
            response = (
                f"Command failed with exit code {result['return_code']}:\\n{result['command']}\\n"
            )
            if duration > 0:
                response += f"Duration: {duration:.3f}s\\n\\n"
            else:
                response += "\\n"

            if result["stderr"]:
                response += f"Error:\\n{result['stderr']}"
            if result["stdout"]:
                response += f"\\nOutput:\\n{result['stdout']}"

        return response

    except Exception as e:
        logger.error(f"Error formatting command result: {e}")

        return f"Error formatting command result: {str(e)}"


def create_fastmcp_server(
    enable_auth: bool = False,
    resource_server_url: Optional[str] = None,
    required_scopes: Optional[List[str]] = None,
) -> fastmcp.FastMCP:
    """
    Create a FastMCP server with container tools.

    Args:
        enable_auth: Enable OAuth 2.1 authentication with Microsoft Entra ID
        resource_server_url: URL of this MCP server
        required_scopes: List of OAuth scopes required for authentication

    Returns:
        FastMCP server instance with optional OAuth authentication
    """

    logger.info("Creating FastMCP server instance...")

    # Configure OAuth authentication if enabled
    if enable_auth:
        try:
            # Get Azure configuration from environment variables
            tenant_id = os.getenv("ENTRA_TENANT_ID")
            client_id = os.getenv("ENTRA_CLIENT_ID")
            client_secret = os.getenv("ENTRA_CLIENT_SECRET")
            env_scopes = os.getenv("ENTRA_REQUIRED_SCOPES")

            if not tenant_id:
                raise ValueError("ENTRA_TENANT_ID environment variable is required")
            if not client_id:
                raise ValueError("ENTRA_CLIENT_ID environment variable is required")
            if not client_secret:
                raise ValueError("ENTRA_CLIENT_SECRET environment variable is required")
            if not env_scopes:
                raise ValueError("ENTRA_REQUIRED_SCOPES environment variable is required")

            if env_scopes:
                # Parse scopes from environment (comma or space separated)
                scopes_list = [s.strip() for s in env_scopes.replace(",", " ").split() if s.strip()]
            elif required_scopes:
                scopes_list = required_scopes
            else:
                raise ValueError(
                    "At least one scope must be specified via ENTRA_REQUIRED_SCOPES or --required-scopes"
                )
            logger.info(client_id)
            # Create AzureProvider with configuration
            auth_provider = AzureProvider(
                client_id=client_id,
                client_secret=client_secret,
                tenant_id=tenant_id,
                base_url=resource_server_url
                or os.getenv("MCP_SERVER_BASE_URL", "http://localhost:8765"),
                required_scopes=scopes_list,
            )

            logger.info(f"  Resource Server URL: {resource_server_url or 'http://localhost:8765'}")
            logger.info(f"  Required Scopes: {required_scopes or []}")

            # Create FastMCP with OAuth
            mcp = fastmcp.FastMCP("ACMS", auth=auth_provider)

            logger.info("FastMCP server created with OAuth authentication")

        except Exception as e:
            logger.error(f"Failed to initialize OAuth authentication: {e}", exc_info=True)
            logger.critical("Cannot start server without valid OAuth configuration. Exiting.")
            sys.exit(1)
    else:
        mcp = fastmcp.FastMCP("ACMS")

    @mcp.tool(
        description="List containers with formatting options",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_container_list(
        all: bool = False, quiet: bool = False, format: str = "table"
    ) -> str:
        cmd_args = ["list"]
        if all:
            cmd_args.append("--all")
        if quiet:
            cmd_args.append("--quiet")
        if format != "table":
            cmd_args.extend(["--format", format])

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Show container system status",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_system_status() -> str:
        result = await run_container_command("system", "status")
        formatted_result = format_command_result(result)

        return formatted_result

    @mcp.tool(
        description="Run a command in a new container with full parameter support",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def acms_container_run(
        image: str,
        command: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        env: Optional[List[str]] = None,
        env_file: Optional[str] = None,
        uid: Optional[int] = None,
        gid: Optional[int] = None,
        interactive: bool = False,
        tty: bool = False,
        user: Optional[str] = None,
        cpus: Optional[float] = None,
        memory: Optional[str] = None,
        detach: bool = False,
        entrypoint: Optional[str] = None,
        mount: Optional[List[str]] = None,
        publish: Optional[List[str]] = None,
        publish_socket: Optional[List[str]] = None,
        tmpfs: Optional[List[str]] = None,
        name: Optional[str] = None,
        remove: bool = False,
        os: str = "linux",
        arch: str = "arm64",
        volume: Optional[List[str]] = None,
        kernel: Optional[str] = None,
        network: Optional[str] = None,
        cidfile: Optional[str] = None,
        no_dns: bool = False,
        dns: Optional[List[str]] = None,
        dns_domain: Optional[str] = None,
        dns_search: Optional[List[str]] = None,
        dns_option: Optional[List[str]] = None,
        label: Optional[List[str]] = None,
        virtualization: bool = False,
        scheme: str = "auto",
        disable_progress_updates: bool = False,
    ) -> str:
        try:
            # Validate all array parameters
            validated_command = (
                validate_array_parameter(command, "command") if command is not None else None
            )
            validated_env = validate_array_parameter(env, "env") if env is not None else None
            validated_mount = (
                validate_array_parameter(mount, "mount") if mount is not None else None
            )
            validated_publish = (
                validate_array_parameter(publish, "publish") if publish is not None else None
            )
            validated_publish_socket = (
                validate_array_parameter(publish_socket, "publish_socket")
                if publish_socket is not None
                else None
            )
            validated_tmpfs = (
                validate_array_parameter(tmpfs, "tmpfs") if tmpfs is not None else None
            )
            validated_volume = (
                validate_array_parameter(volume, "volume") if volume is not None else None
            )
            validated_dns = validate_array_parameter(dns, "dns") if dns is not None else None
            validated_dns_search = (
                validate_array_parameter(dns_search, "dns_search")
                if dns_search is not None
                else None
            )
            validated_dns_option = (
                validate_array_parameter(dns_option, "dns_option")
                if dns_option is not None
                else None
            )
            validated_label = (
                validate_array_parameter(label, "label") if label is not None else None
            )

            cmd_args = ["run"]

            if cwd:
                cmd_args.extend(["--cwd", cwd])
            if validated_env:
                for e in validated_env:
                    cmd_args.extend(["--env", e])
            if env_file:
                cmd_args.extend(["--env-file", env_file])
            if uid is not None:
                cmd_args.extend(["--uid", str(uid)])
            if gid is not None:
                cmd_args.extend(["--gid", str(gid)])
            if interactive:
                cmd_args.append("--interactive")
            if tty:
                cmd_args.append("--tty")
            if user:
                cmd_args.extend(["--user", user])
            if cpus is not None:
                cmd_args.extend(["--cpus", str(cpus)])
            if memory:
                cmd_args.extend(["--memory", memory])
            if detach:
                cmd_args.append("--detach")
            if entrypoint:
                cmd_args.extend(["--entrypoint", entrypoint])
            if validated_mount:
                for m in validated_mount:
                    cmd_args.extend(["--mount", m])
            if validated_publish:
                for p in validated_publish:
                    cmd_args.extend(["--publish", p])
            if validated_publish_socket:
                for ps in validated_publish_socket:
                    cmd_args.extend(["--publish-socket", ps])
            if validated_tmpfs:
                for t in validated_tmpfs:
                    cmd_args.extend(["--tmpfs", t])
            if name:
                cmd_args.extend(["--name", name])
            if remove:
                cmd_args.append("--remove")
            if os != "linux":
                cmd_args.extend(["--os", os])
            if arch != "arm64":
                cmd_args.extend(["--arch", arch])
            if validated_volume:
                for v in validated_volume:
                    cmd_args.extend(["--volume", v])
            if kernel:
                cmd_args.extend(["--kernel", kernel])
            if network:
                cmd_args.extend(["--network", network])
            if cidfile:
                cmd_args.extend(["--cidfile", cidfile])
            if no_dns:
                cmd_args.append("--no-dns")
            if validated_dns:
                for d in validated_dns:
                    cmd_args.extend(["--dns", d])
            if dns_domain:
                cmd_args.extend(["--dns-domain", dns_domain])
            if validated_dns_search:
                for ds in validated_dns_search:
                    cmd_args.extend(["--dns-search", ds])
            if validated_dns_option:
                for do in validated_dns_option:
                    cmd_args.extend(["--dns-option", do])
            if validated_label:
                for lbl in validated_label:
                    cmd_args.extend(["--label", lbl])
            if virtualization:
                cmd_args.append("--virtualization")
            if scheme != "auto":
                cmd_args.extend(["--scheme", scheme])
            if disable_progress_updates:
                cmd_args.append("--disable-progress-updates")

            cmd_args.append(image)
            if validated_command:
                cmd_args.extend(validated_command)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in container_run: {e}")
            return f"Parameter validation error: {str(e)}"

    @mcp.tool(
        description="Create a new container from an image without starting it",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def acms_container_create(
        image: str,
        command: Optional[List[str]] = None,
        name: Optional[str] = None,
        env: Optional[List[str]] = None,
        publish: Optional[List[str]] = None,
        volume: Optional[List[str]] = None,
        mount: Optional[List[str]] = None,
        network: Optional[str] = None,
        label: Optional[List[str]] = None,
        user: Optional[str] = None,
        entrypoint: Optional[str] = None,
    ) -> str:
        try:
            # Validate all array parameters
            validated_command = (
                validate_array_parameter(command, "command") if command is not None else None
            )
            validated_env = validate_array_parameter(env, "env") if env is not None else None
            validated_publish = (
                validate_array_parameter(publish, "publish") if publish is not None else None
            )
            validated_volume = (
                validate_array_parameter(volume, "volume") if volume is not None else None
            )
            validated_mount = (
                validate_array_parameter(mount, "mount") if mount is not None else None
            )
            validated_label = (
                validate_array_parameter(label, "label") if label is not None else None
            )

            cmd_args = ["create"]

            if name:
                cmd_args.extend(["--name", name])
            if validated_env:
                for e in validated_env:
                    cmd_args.extend(["--env", e])
            if validated_publish:
                for p in validated_publish:
                    cmd_args.extend(["--publish", p])
            if validated_volume:
                for v in validated_volume:
                    cmd_args.extend(["--volume", v])
            if validated_mount:
                for m in validated_mount:
                    cmd_args.extend(["--mount", m])
            if network:
                cmd_args.extend(["--network", network])
            if validated_label:
                for lbl in validated_label:
                    cmd_args.extend(["--label", lbl])
            if user:
                cmd_args.extend(["--user", user])
            if entrypoint:
                cmd_args.extend(["--entrypoint", entrypoint])

            cmd_args.append(image)
            if validated_command:
                cmd_args.extend(validated_command)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in container_create: {e}")
            return f"Parameter validation error: {str(e)}"

    @mcp.tool(
        description="Start a stopped container with attachment options",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_container_start(
        container: str, attach: bool = False, interactive: bool = False
    ) -> str:
        cmd_args = ["start"]
        if attach:
            cmd_args.append("--attach")
        if interactive:
            cmd_args.append("--interactive")
        cmd_args.append(container)

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Pull an image from a registry with platform and scheme support",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def acms_image_pull(
        reference: str,
        platform: Optional[str] = None,
        scheme: str = "auto",
        disable_progress_updates: bool = False,
    ) -> str:
        cmd_args = ["image", "pull"]
        if platform:
            cmd_args.extend(["--platform", platform])
        if scheme != "auto":
            cmd_args.extend(["--scheme", scheme])
        if disable_progress_updates:
            cmd_args.append("--disable-progress-updates")
        cmd_args.append(reference)

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="List all images with formatting options",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_image_list(
        quiet: bool = False, verbose: bool = False, format: str = "table"
    ) -> str:
        cmd_args = ["image", "ls"]
        if quiet:
            cmd_args.append("--quiet")
        if verbose:
            cmd_args.append("--verbose")
        if format != "table":
            cmd_args.extend(["--format", format])

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    # CONTAINER MANAGEMENT TOOLS

    @mcp.tool(
        description="Stop running containers gracefully by sending a signal",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_container_stop(
        containers: List[str], signal: str = "SIGTERM", time: Optional[float] = None
    ) -> str:
        try:
            # Validate containers parameter
            validated_containers = validate_array_parameter(containers, "containers")
            if not validated_containers:
                raise ValueError(
                    "Parameter 'containers' is required and cannot be empty. Provide at least one container name to stop."
                )

            cmd_args = ["stop"]
            if signal != "SIGTERM":
                cmd_args.extend(["--signal", signal])
            if time is not None:
                cmd_args.extend(["--time", str(time)])

            cmd_args.extend(validated_containers)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in container_stop: {e}")
            return f"Parameter validation error: {str(e)}"

    @mcp.tool(
        description="Stop all running containers gracefully by sending a signal",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_container_stop_all(signal: str = "SIGTERM", time: Optional[float] = None) -> str:
        cmd_args = ["stop", "--all"]
        if signal != "SIGTERM":
            cmd_args.extend(["--signal", signal])
        if time is not None:
            cmd_args.extend(["--time", str(time)])

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Immediately kill running containers by sending a signal",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_container_kill(containers: List[str], signal: str = "KILL") -> str:
        try:
            # Validate containers parameter
            validated_containers = validate_array_parameter(containers, "containers")
            if not validated_containers:
                raise ValueError(
                    "Parameter 'containers' is required and cannot be empty. Provide at least one container name to kill."
                )

            cmd_args = ["kill"]
            if signal != "KILL":
                cmd_args.extend(["--signal", signal])

            cmd_args.extend(validated_containers)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in container_kill: {e}")
            return f"Parameter validation error: {str(e)}"

    @mcp.tool(
        description="Immediately kill all running containers by sending a signal",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_container_kill_all(signal: str = "KILL") -> str:
        cmd_args = ["kill", "--all"]
        if signal != "KILL":
            cmd_args.extend(["--signal", signal])

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Remove one or more containers",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_container_delete(containers: List[str], force: bool = False) -> str:
        try:
            # Validate containers parameter
            validated_containers = validate_array_parameter(containers, "containers")
            if not validated_containers:
                raise ValueError(
                    "Parameter 'containers' is required and cannot be empty. Provide at least one container name to delete."
                )

            cmd_args = ["rm"]
            if force:
                cmd_args.append("--force")

            cmd_args.extend(validated_containers)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in container_delete: {e}")
            return f"Parameter validation error: {str(e)}"

    @mcp.tool(
        description="Remove all containers",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_container_delete_all(force: bool = False) -> str:
        cmd_args = ["rm", "--all"]
        if force:
            cmd_args.append("--force")

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Execute a command inside a running container",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def acms_container_exec(
        container: str,
        command: str,
        interactive: bool = False,
        tty: bool = False,
        user: Optional[str] = None,
        env: Optional[List[str]] = None,
    ) -> str:
        try:
            # Validate array parameters
            validated_env = validate_array_parameter(env, "env") if env is not None else None

            cmd_args = ["exec"]
            if interactive:
                cmd_args.append("--interactive")
            if tty:
                cmd_args.append("--tty")
            if user:
                cmd_args.extend(["--user", user])
            if validated_env:
                for e in validated_env:
                    cmd_args.extend(["--env", e])

            # Add container name
            cmd_args.append(container)

            # Split command string into arguments for proper execution
            import shlex

            try:
                command_args = shlex.split(command)
                cmd_args.extend(command_args)
            except ValueError as shlex_error:
                logger.warning(
                    f"Failed to parse command with shlex, using as single argument: {shlex_error}"
                )
                cmd_args.append(command)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in container_exec: {e}")
            return f"Parameter validation error: {str(e)}"

    @mcp.tool(
        description="Fetch logs from a container",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_container_logs(
        container: str,
        follow: bool = False,
        boot: bool = False,
        n: Optional[int] = None,
    ) -> str:
        cmd_args = ["logs"]
        if follow:
            cmd_args.append("--follow")
        if boot:
            cmd_args.append("--boot")
        if n is not None:
            cmd_args.extend(["-n", str(n)])
        cmd_args.append(container)

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Display detailed container information in JSON",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_container_inspect(container: str) -> str:
        result = await run_container_command("inspect", container)
        return format_command_result(result)

    # IMAGE MANAGEMENT TOOLS

    @mcp.tool(
        description="Push an image to a registry",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def acms_image_push(
        reference: str,
        platform: Optional[str] = None,
        scheme: str = "auto",
        disable_progress_updates: bool = False,
    ) -> str:
        cmd_args = ["image", "push"]
        if platform:
            cmd_args.extend(["--platform", platform])
        if scheme != "auto":
            cmd_args.extend(["--scheme", scheme])
        if disable_progress_updates:
            cmd_args.append("--disable-progress-updates")
        cmd_args.append(reference)

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Save an image to a tar archive",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_image_save(
        reference: str, output: Optional[str] = None, platform: Optional[str] = None
    ) -> str:
        cmd_args = ["image", "save"]
        if platform:
            cmd_args.extend(["--platform", platform])
        if output:
            cmd_args.extend(["--output", output])
        cmd_args.append(reference)

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Load images from a tar archive",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_image_load(input: Optional[str] = None) -> str:
        cmd_args = ["image", "load"]
        if input:
            cmd_args.extend(["--input", input])

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Apply a new tag to an existing image",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_image_tag(source_image: str, target_image: str) -> str:
        result = await run_container_command("image", "tag", source_image, target_image)
        return format_command_result(result)

    @mcp.tool(
        description="Remove one or more images",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_image_delete(images: List[str]) -> str:
        try:
            # Validate images parameter
            validated_images = validate_array_parameter(images, "images")
            if not validated_images:
                raise ValueError(
                    "Parameter 'images' is required and cannot be empty. Provide at least one image name to delete."
                )

            cmd_args = ["image", "rm"]
            cmd_args.extend(validated_images)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in image_delete: {e}")
            return f"Parameter validation error: {str(e)}"

    @mcp.tool(
        description="Remove all images",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_image_delete_all() -> str:
        result = await run_container_command("image", "rm", "--all")
        return format_command_result(result)

    @mcp.tool(
        description="Remove unused (dangling) images",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_image_prune() -> str:
        result = await run_container_command("image", "prune")
        return format_command_result(result)

    @mcp.tool(
        description="Show detailed information for one or more images in JSON",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_image_inspect(image: str) -> str:
        result = await run_container_command("image", "inspect", image)
        return format_command_result(result)

    # BUILD OPERATION TOOLS

    @mcp.tool(
        description="Build an OCI image from a local build context",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def acms_container_build(
        path: str,
        tag: Optional[str] = None,
        file: Optional[str] = None,
        build_arg: Optional[List[str]] = None,
        label: Optional[List[str]] = None,
        no_cache: bool = False,
        target: Optional[str] = None,
        arch: str = "arm64",
        os: str = "linux",
        cpus: float = 2.0,
        memory: str = "2048MB",
        quiet: bool = False,
    ) -> str:
        try:
            # Validate array parameters
            validated_build_arg = (
                validate_array_parameter(build_arg, "build_arg") if build_arg is not None else None
            )
            validated_label = (
                validate_array_parameter(label, "label") if label is not None else None
            )

            cmd_args = ["build"]
            if tag:
                cmd_args.extend(["--tag", tag])
            if file:
                cmd_args.extend(["--file", file])
            if validated_build_arg:
                for arg in validated_build_arg:
                    cmd_args.extend(["--build-arg", arg])
            if validated_label:
                for lbl in validated_label:
                    cmd_args.extend(["--label", lbl])
            if no_cache:
                cmd_args.append("--no-cache")
            if target:
                cmd_args.extend(["--target", target])
            if arch != "arm64":
                cmd_args.extend(["--arch", arch])
            if os != "linux":
                cmd_args.extend(["--os", os])
            if cpus != 2.0:
                cmd_args.extend(["--cpus", str(cpus)])
            if memory != "2048MB":
                cmd_args.extend(["--memory", memory])
            if quiet:
                cmd_args.append("--quiet")
            cmd_args.append(path)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in container_build: {e}")
            return f"Parameter validation error: {str(e)}"

    # BUILDER MANAGEMENT TOOLS

    @mcp.tool(
        description="Start the BuildKit builder container",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_builder_start(cpus: float = 2.0, memory: str = "2048MB") -> str:
        cmd_args = ["builder", "start"]
        if cpus != 2.0:
            cmd_args.extend(["--cpus", str(cpus)])
        if memory != "2048MB":
            cmd_args.extend(["--memory", memory])

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Show the current status of the BuildKit builder",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_builder_status(json: bool = False) -> str:
        cmd_args = ["builder", "status"]
        if json:
            cmd_args.append("--json")

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Stop the BuildKit builder",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_builder_stop() -> str:
        result = await run_container_command("builder", "stop")
        return format_command_result(result)

    @mcp.tool(
        description="Remove the BuildKit builder container",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_builder_delete(force: bool = False) -> str:
        cmd_args = ["builder", "rm"]
        if force:
            cmd_args.append("--force")

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    # NETWORK MANAGEMENT TOOLS

    @mcp.tool(
        description="Create a new network",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def acms_network_create(name: str) -> str:
        result = await run_container_command("network", "create", name)
        return format_command_result(result)

    @mcp.tool(
        description="Delete one or more networks",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_network_delete(networks: List[str]) -> str:
        try:
            # Validate networks parameter
            validated_networks = validate_array_parameter(networks, "networks")
            if not validated_networks:
                raise ValueError(
                    "Parameter 'networks' is required and cannot be empty. Provide at least one network name to delete."
                )

            cmd_args = ["network", "rm"]
            cmd_args.extend(validated_networks)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in network_delete: {e}")
            return f"Parameter validation error: {str(e)}"

    @mcp.tool(
        description="Delete all networks",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_network_delete_all() -> str:
        result = await run_container_command("network", "rm", "--all")
        return format_command_result(result)

    @mcp.tool(
        description="List user-defined networks",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_network_list(quiet: bool = False, format: str = "table") -> str:
        cmd_args = ["network", "ls"]
        if quiet:
            cmd_args.append("--quiet")
        if format != "table":
            cmd_args.extend(["--format", format])

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Show detailed information about networks",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_network_inspect(name: str) -> str:
        result = await run_container_command("network", "inspect", name)
        return format_command_result(result)

    # VOLUME MANAGEMENT TOOLS

    @mcp.tool(
        description="Create a new volume",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def acms_volume_create(
        name: str,
        size: Optional[str] = None,
        opt: Optional[List[str]] = None,
        label: Optional[List[str]] = None,
    ) -> str:
        try:
            # Validate array parameters
            validated_opt = validate_array_parameter(opt, "opt") if opt is not None else None
            validated_label = (
                validate_array_parameter(label, "label") if label is not None else None
            )

            cmd_args = ["volume", "create"]
            if size:
                cmd_args.extend(["-s", size])
            if validated_opt:
                for option in validated_opt:
                    cmd_args.extend(["--opt", option])
            if validated_label:
                for lbl in validated_label:
                    cmd_args.extend(["--label", lbl])
            cmd_args.append(name)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in volume_create: {e}")
            return f"Parameter validation error: {str(e)}"

    @mcp.tool(
        description="Remove one or more volumes",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_volume_delete(names: List[str]) -> str:
        try:
            # Validate names parameter
            validated_names = validate_array_parameter(names, "names")
            if not validated_names:
                raise ValueError(
                    "Parameter 'names' is required and cannot be empty. Provide at least one volume name to delete."
                )

            cmd_args = ["volume", "rm"]
            cmd_args.extend(validated_names)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in volume_delete: {e}")
            return f"Parameter validation error: {str(e)}"

    @mcp.tool(
        description="List volumes",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_volume_list(quiet: bool = False, format: str = "table") -> str:
        cmd_args = ["volume", "ls"]
        if quiet:
            cmd_args.append("--quiet")
        if format != "table":
            cmd_args.extend(["--format", format])

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Display detailed information for volumes",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_volume_inspect(names: List[str]) -> str:
        try:
            # Validate names parameter
            validated_names = validate_array_parameter(names, "names")
            if not validated_names:
                raise ValueError(
                    "Parameter 'names' is required and cannot be empty. Provide at least one volume name to inspect."
                )

            cmd_args = ["volume", "inspect"]
            cmd_args.extend(validated_names)

            result = await run_container_command(*cmd_args)
            return format_command_result(result)
        except ValueError as e:
            logger.error(f"Parameter validation error in volume_inspect: {e}")
            return f"Parameter validation error: {str(e)}"

    # REGISTRY MANAGEMENT TOOLS

    @mcp.tool(
        description="Authenticate with a registry",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def acms_registry_login(
        server: str,
        username: Optional[str] = None,
        password_stdin: bool = False,
        scheme: str = "auto",
    ) -> str:
        cmd_args = ["registry", "login"]
        if username:
            cmd_args.extend(["--username", username])
        if password_stdin:
            cmd_args.append("--password-stdin")
        if scheme != "auto":
            cmd_args.extend(["--scheme", scheme])
        cmd_args.append(server)

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Log out of a registry",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def acms_registry_logout(server: str) -> str:
        result = await run_container_command("registry", "logout", server)
        return format_command_result(result)

    # SYSTEM MANAGEMENT TOOLS

    @mcp.tool(
        description="Start the container services",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_system_start(
        app_root: Optional[str] = None,
        install_root: Optional[str] = None,
        enable_kernel_install: bool = False,
        disable_kernel_install: bool = False,
    ) -> str:
        cmd_args = ["system", "start"]
        if app_root:
            cmd_args.extend(["--app-root", app_root])
        if install_root:
            cmd_args.extend(["--install-root", install_root])
        if enable_kernel_install:
            cmd_args.append("--enable-kernel-install")
        if disable_kernel_install:
            cmd_args.append("--disable-kernel-install")

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Stop the container services",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_system_stop(prefix: Optional[str] = None) -> str:
        cmd_args = ["system", "stop"]
        if prefix:
            cmd_args.extend(["--prefix", prefix])

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Display logs from the container services",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_system_logs(last: str = "5m", follow: bool = False) -> str:
        cmd_args = ["system", "logs"]
        if last != "5m":
            cmd_args.extend(["--last", last])
        if follow:
            cmd_args.append("--follow")

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="Create a local DNS domain for containers",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def acms_system_dns_create(name: str) -> str:
        result = await run_container_command("system", "dns", "create", name)
        return format_command_result(result)

    @mcp.tool(
        description="Delete a local DNS domain",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_system_dns_delete(name: str) -> str:
        result = await run_container_command("system", "dns", "rm", name)
        return format_command_result(result)

    @mcp.tool(
        description="List configured local DNS domains",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_system_dns_list() -> str:
        result = await run_container_command("system", "dns", "ls")
        return format_command_result(result)

    @mcp.tool(
        description="Install or update the Linux kernel",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_system_kernel_set(
        binary: Optional[str] = None,
        tar: Optional[str] = None,
        arch: Optional[str] = None,
        recommended: bool = False,
    ) -> str:
        cmd_args = ["system", "kernel", "set"]
        if binary:
            cmd_args.extend(["--binary", binary])
        if tar:
            cmd_args.extend(["--tar", tar])
        if arch:
            cmd_args.extend(["--arch", arch])
        if recommended:
            cmd_args.append("--recommended")

        result = await run_container_command(*cmd_args)
        return format_command_result(result)

    @mcp.tool(
        description="List all system properties",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_system_property_list() -> str:
        result = await run_container_command("system", "property", "list")
        return format_command_result(result)

    @mcp.tool(
        description="Get the value of a system property",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_system_property_get(key: str) -> str:
        result = await run_container_command("system", "property", "get", key)
        return format_command_result(result)

    @mcp.tool(
        description="Set a system property value",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_system_property_set(key: str, value: str) -> str:
        result = await run_container_command("system", "property", "set", key, value)
        return format_command_result(result)

    @mcp.tool(
        description="Clear a system property",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def acms_system_property_clear(key: str) -> str:
        result = await run_container_command("system", "property", "clear", key)
        return format_command_result(result)

    # Create a custom connection logger that will track all MCP connections
    connection_logger = logging.getLogger("mcp-connections")
    connection_logger.info("Connection logger initialized")

    return mcp


def parse_arguments():
    """Parse command line arguments for ACMS server configuration."""
    parser = argparse.ArgumentParser(
        description="Apple Container MCP Server (ACMS)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 acms.py --ssl --port 8443        # HTTPS server with SSL on port 8443
  python3 acms.py --host 127.0.0.1   # HTTP server on localhost""",
    )

    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=8765,
        help="Port to bind the server to (default: 8765)",
    )

    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host/IP to bind the server to (default: 127.0.0.1)",
    )

    parser.add_argument(
        "--ssl",
        action="store_true",
        help="Enable SSL/TLS (HTTPS) mode. Requires server.crt and server.key in current directory",
    )

    parser.add_argument(
        "--cert-file",
        type=str,
        default="server.crt",
        help="Path to SSL certificate file (default: server.crt)",
    )

    parser.add_argument(
        "--key-file",
        type=str,
        default="server.key",
        help="Path to SSL private key file (default: server.key)",
    )

    parser.add_argument(
        "--http",
        type=int,
        dest="port",
        help="Legacy: specify port for HTTP mode (same as --port)",
    )

    parser.add_argument(
        "--enable-auth",
        action="store_true",
        help="Enable OAuth 2.1 authentication with Microsoft Entra ID (requires .env configuration)",
    )

    parser.add_argument(
        "--resource-url",
        type=str,
        help="Resource server URL for OAuth identification (default: http://127.0.0.1:PORT)",
    )

    parser.add_argument(
        "--required-scopes",
        type=str,
        nargs="*",
        help="Required OAuth scopes for authentication (space-separated)",
    )

    return parser.parse_args()


async def main() -> None:
    logger.info("=" * 50)
    logger.info("Starting Apple Container MCP Server (ACMS)")
    logger.info("=" * 50)

    # Parse command line arguments
    args = parse_arguments()

    port = args.port
    host = args.host
    use_ssl = args.ssl
    cert_file = args.cert_file
    key_file = args.key_file
    enable_auth = args.enable_auth
    required_scopes = args.required_scopes if args.required_scopes else []

    # Determine resource server URL
    protocol = "https" if use_ssl else "http"
    resource_url = args.resource_url if args.resource_url else f"{protocol}://{host}:{port}"

    # Log environment info
    if not check_container_available():
        logger.critical("container CLI not found. Please install Apple's container tool.")
        logger.critical("See: https://github.com/apple/container for installation instructions.")

    # Validate SSL configuration if SSL is enabled
    if use_ssl:
        if not os.path.isfile(cert_file):
            logger.critical(f"SSL certificate file not found: {cert_file}")
            logger.critical(
                "Please ensure the certificate file exists or use --cert-file to specify the correct path"
            )
            sys.exit(1)

        if not os.path.isfile(key_file):
            logger.critical(f"SSL private key file not found: {key_file}")
            logger.critical(
                "Please ensure the private key file exists or use --key-file to specify the correct path"
            )
            sys.exit(1)

        logger.info(f"SSL enabled - using cert: {cert_file}, key: {key_file}")

    logger.info(f"ACMS: {resource_url}/mcp")

    try:
        # Create FastMCP server with optional OAuth authentication
        mcp = create_fastmcp_server(
            enable_auth=enable_auth,
            resource_server_url=resource_url,
            required_scopes=required_scopes,
        )

        # Get the HTTP app
        app = mcp.http_app()

        # Custom uvicorn config for maximum logging
        config_kwargs = {
            "app": app,
            "host": host,
            "port": port,
            "log_level": "debug",
            "access_log": True,
            "use_colors": True,
            "loop": "asyncio",
            "ws": "websockets-sansio",
        }

        # Add SSL configuration if enabled
        if use_ssl:
            config_kwargs.update({"ssl_certfile": cert_file, "ssl_keyfile": key_file})
            logger.info(f"SSL configuration: certfile={cert_file}, keyfile={key_file}")

        config = uvicorn.Config(**config_kwargs)

        # Create and run server
        server = uvicorn.Server(config)

        # Run the server (this blocks until shutdown)
        await server.serve()

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down gracefully")
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        raise
    finally:
        logger.info("ACMS shutdown complete")


def cli_main():
    """Entry point for the pip-installed acms command."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Server stopped by user", file=sys.stderr)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    cli_main()
