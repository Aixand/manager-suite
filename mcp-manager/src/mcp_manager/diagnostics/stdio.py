"""Bounded stdio status probes for project preparation adapters."""
import json
import queue
import subprocess
import threading
import time

from .._version import __version__
from ..errors import McpError
from ..storage.files import operational

@operational
def probe(row, tool, arguments=None):
    """Bounded stdio read-only preset check, independent of provider credentials."""
    process = subprocess.Popen([row["command"], *row["args"]],
        cwd=row.get("cwd"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True)
    inbox = queue.Queue(maxsize=32)
    def reader():
        try:
            while True:
                line = process.stdout.readline(1024 * 1024)
                if not line or not line.endswith("\n"):
                    break
                message = json.loads(line)
                if not isinstance(message, dict):
                    break
                inbox.put_nowait(message)
        except (ValueError, OSError, queue.Full):
            pass
        try:
            inbox.put_nowait(None)
        except queue.Full:
            pass
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    def request(identifier, method, params):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + row.get("startup_timeout_sec", 60)
        while True:
            try:
                response = inbox.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise McpError("timeout", "MCP preset check timed out") from None
            if response is None:
                raise McpError("transport", "MCP preset disconnected during its check")
            if response.get("id") == identifier:
                if "error" in response:
                    raise McpError("integrity", "MCP preset rejected its status check")
                result = response["result"]
                if not isinstance(result, dict):
                    raise McpError("integrity", "MCP preset returned an invalid response")
                return result
    try:
        request(1, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "agent-manager-check", "version": __version__}})
        process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        result = request(2, "tools/call", {"name": tool, "arguments": arguments or {}})
        if result.get("isError"):
            raise McpError("integrity", "MCP preset status check failed")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        for block in result.get("content", []):
            if not isinstance(block, dict):
                raise McpError("integrity", "MCP preset returned an invalid content block")
            if block.get("type") == "text":
                status = json.loads(block["text"])
                if not isinstance(status, dict):
                    raise McpError("integrity", "MCP preset status must be an object")
                return status
        raise McpError("integrity", "MCP preset returned no structured status")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise McpError("integrity", "Cannot validate MCP preset status") from error
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()
        thread.join(timeout=1)
