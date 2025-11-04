import pytest
from unittest.mock import AsyncMock, patch


class TestArgumentValidation:
    """Test argument validation functions."""

    def test_validate_container_arg_accepts_valid_strings(self):
        """Verify that valid arguments pass validation."""
        from acms import _validate_container_arg

        valid_args = [
            "ubuntu",
            "list",
            "--all",
            "container-name",
            "/path/to/file",
            "key=value",
        ]

        for arg in valid_args:
            result = _validate_container_arg(arg)
            assert result == arg

    def test_validate_container_arg_rejects_dangerous_chars(self):
        """Verify that dangerous characters are rejected."""
        from acms import _validate_container_arg

        dangerous_args = [
            "test;rm -rf /",
            "test|cat /etc/passwd",
            "test&whoami",
            "test$PATH",
            "test`whoami`",
            "test\nrm -rf",
        ]

        for arg in dangerous_args:
            with pytest.raises(ValueError, match="Invalid character"):
                _validate_container_arg(arg)

    def test_validate_container_arg_rejects_non_strings(self):
        """Verify that non-string arguments are rejected."""
        from acms import _validate_container_arg

        with pytest.raises(ValueError, match="must be a string"):
            _validate_container_arg(123)

        with pytest.raises(ValueError, match="must be a string"):
            _validate_container_arg(["list"])


class TestArrayParameterValidation:
    """Test array parameter validation and normalization."""

    def test_validate_array_accepts_none(self):
        """Verify None is accepted as valid."""
        from acms import validate_array_parameter

        result = validate_array_parameter(None, "test_param")
        assert result is None

    def test_validate_array_accepts_string_list(self):
        """Verify lists of strings are accepted."""
        from acms import validate_array_parameter

        input_list = ["item1", "item2", "item3"]
        result = validate_array_parameter(input_list, "test_param")
        assert result == input_list

    def test_validate_array_accepts_json_string(self):
        """Verify JSON array strings are parsed correctly."""
        from acms import validate_array_parameter

        json_str = '["item1", "item2", "item3"]'
        result = validate_array_parameter(json_str, "test_param")
        assert result == ["item1", "item2", "item3"]

    def test_validate_array_converts_single_string(self):
        """Verify single non-JSON strings are converted to list."""
        from acms import validate_array_parameter

        result = validate_array_parameter("single-item", "test_param")
        assert result == ["single-item"]

    def test_validate_array_rejects_empty_list(self):
        """Verify empty arrays are rejected."""
        from acms import validate_array_parameter

        with pytest.raises(ValueError, match="cannot be an empty array"):
            validate_array_parameter([], "test_param")

    def test_validate_array_rejects_mixed_types(self):
        """Verify lists with non-string elements are rejected."""
        from acms import validate_array_parameter

        mixed_list = ["string", 123, "another"]
        with pytest.raises(ValueError, match="must be a list of strings"):
            validate_array_parameter(mixed_list, "test_param")

    def test_validate_array_rejects_invalid_json(self):
        """Verify invalid JSON objects are handled correctly."""
        from acms import validate_array_parameter

        # JSON object instead of array
        json_obj = '{"key": "value"}'
        with pytest.raises(ValueError, match="must be an array of strings"):
            validate_array_parameter(json_obj, "test_param")


class TestCommandResultFormatting:
    """Test command result formatting."""

    def test_format_successful_command_result(self):
        """Verify successful command results are formatted correctly."""
        from acms import format_command_result

        result = {
            "command": "container list",
            "return_code": 0,
            "stdout": "Container output",
            "stderr": "",
        }

        formatted = format_command_result(result)
        assert "executed successfully" in formatted
        assert "container list" in formatted
        assert "Container output" in formatted

    def test_format_failed_command_result(self):
        """Verify failed command results are formatted correctly."""
        from acms import format_command_result

        result = {
            "command": "container invalid",
            "return_code": 1,
            "stdout": "",
            "stderr": "Error: invalid command",
        }

        formatted = format_command_result(result)
        assert "failed with exit code 1" in formatted
        assert "container invalid" in formatted
        assert "Error: invalid command" in formatted

    def test_format_command_result_with_both_outputs(self):
        """Verify command results with both stdout and stderr are formatted correctly."""
        from acms import format_command_result

        result = {
            "command": "container run ubuntu",
            "return_code": 0,
            "stdout": "Container started",
            "stderr": "Warning: some warning",
        }

        formatted = format_command_result(result)
        assert "Container started" in formatted
        assert "some warning" in formatted


class TestContainerAvailabilityCheck:
    """Test container CLI availability checking."""

    @patch("shutil.which")
    def test_check_container_available_when_present(self, mock_which):
        """Verify availability check returns True when container CLI exists."""
        from acms import check_container_available

        mock_which.return_value = "/usr/local/bin/container"
        result = check_container_available()
        assert result is True
        mock_which.assert_called_once_with("container")

    @patch("shutil.which")
    def test_check_container_available_when_absent(self, mock_which):
        """Verify availability check returns False when container CLI missing."""
        from acms import check_container_available

        mock_which.return_value = None
        result = check_container_available()
        assert result is False
        mock_which.assert_called_once_with("container")


class TestRunContainerCommand:
    """Test the core container command execution function."""

    @pytest.mark.asyncio
    async def test_run_container_command_validation(self):
        """Verify command arguments are validated before execution."""
        from acms import run_container_command

        # Should raise ValueError for dangerous arguments
        with pytest.raises(ValueError, match="Invalid command argument"):
            await run_container_command("list", "--all;rm -rf /")

    @pytest.mark.asyncio
    @patch("asyncio.create_subprocess_exec")
    async def test_run_container_command_success(self, mock_subprocess):
        """Verify successful command execution returns correct result."""
        from acms import run_container_command

        # Mock process
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"success output", b""))
        mock_process.returncode = 0
        mock_subprocess.return_value = mock_process

        result = await run_container_command("list")

        assert result["return_code"] == 0
        assert result["stdout"] == "success output"
        assert result["stderr"] == ""
        assert "container list" in result["command"]

    @pytest.mark.asyncio
    @patch("asyncio.create_subprocess_exec")
    async def test_run_container_command_failure(self, mock_subprocess):
        """Verify failed command execution returns correct result."""
        from acms import run_container_command

        # Mock process with error
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b"error message"))
        mock_process.returncode = 1
        mock_subprocess.return_value = mock_process

        result = await run_container_command("invalid")

        assert result["return_code"] == 1
        assert result["stderr"] == "error message"


class TestFastMCPServerCreation:
    """Test FastMCP server creation and configuration."""

    @patch.dict("os.environ", {}, clear=True)
    def test_create_server_without_auth(self):
        """Verify server can be created without authentication."""
        from acms import create_fastmcp_server

        server = create_fastmcp_server(enable_auth=False)
        assert server is not None
        assert hasattr(server, "http_app")

    @patch.dict(
        "os.environ",
        {
            "ENTRA_TENANT_ID": "test-tenant",
            "ENTRA_CLIENT_ID": "test-client",
            "ENTRA_CLIENT_SECRET": "test-secret",
            "ENTRA_REQUIRED_SCOPES": "scope1 scope2",
        },
    )
    def test_create_server_with_auth_env_vars(self):
        """Verify server can be created with auth when env vars are set."""
        from acms import create_fastmcp_server

        server = create_fastmcp_server(
            enable_auth=True, resource_server_url="http://localhost:8765"
        )
        assert server is not None

    @patch.dict("os.environ", {}, clear=True)
    def test_create_server_with_auth_missing_env_vars(self):
        """Verify server creation fails with auth enabled but missing env vars."""
        from acms import create_fastmcp_server

        with pytest.raises(SystemExit):
            create_fastmcp_server(
                enable_auth=True, resource_server_url="http://localhost:8765"
            )


class TestArgumentParsing:
    """Test command-line argument parsing."""

    def test_parse_arguments_defaults(self):
        """Verify default arguments are set correctly."""
        from acms import parse_arguments

        with patch("sys.argv", ["acms.py"]):
            args = parse_arguments()
            assert args.port == 8765
            assert args.host == "127.0.0.1"
            assert args.ssl is False
            assert args.enable_auth is False

    def test_parse_arguments_custom_port(self):
        """Verify custom port is parsed correctly."""
        from acms import parse_arguments

        with patch("sys.argv", ["acms.py", "--port", "9000"]):
            args = parse_arguments()
            assert args.port == 9000

    def test_parse_arguments_ssl_enabled(self):
        """Verify SSL flag is parsed correctly."""
        from acms import parse_arguments

        with patch("sys.argv", ["acms.py", "--ssl"]):
            args = parse_arguments()
            assert args.ssl is True

    def test_parse_arguments_auth_enabled(self):
        """Verify auth flag is parsed correctly."""
        from acms import parse_arguments

        with patch("sys.argv", ["acms.py", "--enable-auth"]):
            args = parse_arguments()
            assert args.enable_auth is True

    def test_parse_arguments_custom_host(self):
        """Verify custom host is parsed correctly."""
        from acms import parse_arguments

        with patch("sys.argv", ["acms.py", "--host", "0.0.0.0"]):
            args = parse_arguments()
            assert args.host == "0.0.0.0"


class TestServerConfiguration:
    """Test server configuration and setup."""

    def test_server_has_required_dependencies(self):
        """Verify all required dependencies are importable."""
        import fastmcp
        import mcp
        import uvicorn
        from dotenv import load_dotenv

        assert fastmcp is not None
        assert mcp is not None
        assert uvicorn is not None
        assert load_dotenv is not None

    def test_command_result_type_alias(self):
        """Verify CommandResult type alias is defined."""
        from acms import CommandResult

        assert CommandResult is not None


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    @patch("asyncio.create_subprocess_exec")
    async def test_run_container_command_handles_unicode(self, mock_subprocess):
        """Verify command handles unicode output correctly."""
        from acms import run_container_command

        # Mock process with unicode output
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(
            return_value=("Test 你好 🚀".encode("utf-8"), b"")
        )
        mock_process.returncode = 0
        mock_subprocess.return_value = mock_process

        result = await run_container_command("list")
        assert "你好" in result["stdout"]
        assert "🚀" in result["stdout"]

    @pytest.mark.asyncio
    @patch("asyncio.create_subprocess_exec")
    async def test_run_container_command_handles_invalid_utf8(self, mock_subprocess):
        """Verify command handles invalid UTF-8 gracefully."""
        from acms import run_container_command

        # Mock process with invalid UTF-8
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"\xff\xfe", b""))
        mock_process.returncode = 0
        mock_subprocess.return_value = mock_process

        result = await run_container_command("list")
        # Should not raise, uses errors='replace'
        assert result["stdout"] is not None

    def test_validate_array_parameter_edge_cases(self):
        """Test edge cases in array parameter validation."""
        from acms import validate_array_parameter

        # Whitespace handling in JSON
        result = validate_array_parameter('  ["a", "b"]  ', "test")
        assert result == ["a", "b"]

        # Single element array
        result = validate_array_parameter(["single"], "test")
        assert result == ["single"]

    def test_format_command_result_handles_missing_fields(self):
        """Verify formatter handles results with missing optional fields."""
        from acms import format_command_result

        minimal_result = {
            "command": "test",
            "return_code": 0,
            "stdout": "",
            "stderr": "",
        }

        formatted = format_command_result(minimal_result)
        assert "test" in formatted
        assert formatted is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
