"""The MCP server as a real process: stdio (what the agent uses) and Streamable HTTP."""

import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp import Client, StdioServerParameters

from secureagent_mcp.config import PACKAGE_ROOT

from .conftest import ALICE, TOKEN


class _Handler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).received.append(body)
        answer = {"trace_id": body["trace_id"], "decision": "ALLOW", "gate": None, "gate_number": None,
                  "reason_code": None, "reason": None, "checks": [], "result": {"echo": body["tool"]}}
        data = json.dumps(answer).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def vault_http():
    _Handler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", _Handler.received
    server.shutdown()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_env(tmp_path, vault_url, **extra):
    lines = {"VAULT_URL": vault_url, "AGENT_TOKEN": TOKEN, "AGENT_ID": "crypto-agent", **extra}
    path = tmp_path / ".env"
    path.write_text("".join(f"{k}={v}\n" for k, v in lines.items()))
    return str(path)


def stdio_params(env_file):
    return StdioServerParameters(
        command=sys.executable, args=["-m", "secureagent_mcp", "--env-file", env_file], cwd=str(PACKAGE_ROOT)
    )


async def test_stdio_end_to_end(tmp_path, vault_http):
    url, received = vault_http
    async with Client(stdio_params(write_env(tmp_path, url))) as client:
        tools = await client.list_tools()
        assert [t.name for t in tools.tools] == ["get_balance", "propose_transfer", "get_transaction_status", "get_policy_status"]
        result = await client.call_tool("propose_transfer", {"asset": "ETH", "amount": "0.05", "recipient": ALICE})
    assert result.structured_content["decision"] == "ALLOW"
    assert result.structured_content["result"] == {"echo": "propose_transfer"}
    assert received[0]["session_token"] == TOKEN          # the server process read it from the .env file
    assert received[0]["args"] == {"asset": "ETH", "amount": "0.05", "recipient": ALICE}


async def test_stdio_vault_down_is_a_denial_not_a_crash(tmp_path):
    dead = f"http://127.0.0.1:{free_port()}"   # nothing listens here
    async with Client(stdio_params(write_env(tmp_path, dead))) as client:
        result = await client.call_tool("get_policy_status", {})
        assert result.is_error is True
        assert result.structured_content["reason_code"] == "VAULT_UNREACHABLE"
        # and the server is still alive for the next call
        again = await client.call_tool("get_policy_status", {})
        assert again.structured_content["reason_code"] == "VAULT_UNREACHABLE"


async def test_stdio_attack_demo_flag_reaches_the_process(tmp_path, vault_http):
    url, _ = vault_http
    async with Client(stdio_params(write_env(tmp_path, url, EXPOSE_ATTACK_DEMO_TOOLS="1"))) as client:
        names = [t.name for t in (await client.list_tools()).tools]
    assert "export_private_key" in names


def test_missing_token_stops_the_server_with_a_clear_message(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("VAULT_URL=http://127.0.0.1:1\n")
    done = subprocess.run(
        [sys.executable, "-m", "secureagent_mcp", "--env-file", str(env_file)],
        cwd=PACKAGE_ROOT, capture_output=True, text=True, timeout=20,
        env={"PATH": "/usr/bin:/bin"},   # nothing from the real environment
    )
    assert done.returncode != 0
    assert "AGENT_TOKEN" in done.stderr
    assert done.stdout == ""   # stdout is the protocol channel: it must stay clean


def test_stdout_stays_clean_while_serving(tmp_path, vault_http):
    # Log lines must go to stderr. Send one initialize by hand and read exactly what comes back on stdout.
    url, _ = vault_http
    env_file = write_env(tmp_path, url)
    proc = subprocess.Popen(
        [sys.executable, "-m", "secureagent_mcp", "--env-file", env_file],
        cwd=PACKAGE_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert json.loads(line)["id"] == 1   # first thing on stdout is valid JSON-RPC, not a log line
    finally:
        proc.kill()
        proc.wait(timeout=10)
        err = proc.stderr.read()
    assert "agent crypto-agent" in err    # the startup log went to stderr instead
    assert TOKEN not in err


async def test_http_transport_end_to_end(tmp_path, vault_http):
    url, received = vault_http
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "secureagent_mcp", "--transport", "http", "--port", str(port),
         "--env-file", write_env(tmp_path, url)],
        cwd=PACKAGE_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("the HTTP server did not start")
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            tools = await client.list_tools()
            assert "propose_transfer" in [t.name for t in tools.tools]
            result = await client.call_tool("get_policy_status", {})
        assert result.structured_content["decision"] == "ALLOW"
        assert received[0]["tool"] == "get_policy_status"
    finally:
        proc.kill()
        proc.wait(timeout=10)
        proc.stdout.close()
        proc.stderr.close()
