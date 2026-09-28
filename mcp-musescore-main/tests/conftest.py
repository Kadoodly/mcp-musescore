import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(*args, stdin=None):
    return subprocess.run([NODE, *args], cwd=ROOT, input=stdin, capture_output=True, text=True, timeout=120)


@pytest.fixture(scope="session")
def plugin_tables():
    """actionParams / sequenceCommands / noteValueTicks as defined in the plugin."""
    if NODE is None:
        pytest.skip("node is not installed")
    res = run_node("tests/js/test_plugin.js", "--tables")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


class FakeClient:
    """Stands in for MuseScoreClient: records commands, answers with success."""

    def __init__(self):
        self.sent = []

    async def connect(self):
        return True

    async def send_command(self, action, params=None):
        self.sent.append((action, params or {}))
        return {"success": True, "message": "ok"}


@pytest.fixture
def server():
    import server as server_module

    client = FakeClient()
    app = server_module.create_server(client)
    return app, client


class MockPluginClient:
    """A MuseScoreClient stand-in that talks to the plugin running on the mock
    MuseScore API in node (tests/js/mock_server.js). Not MuseScore: it checks
    the Python server and the plugin's logic together."""

    def __init__(self, **opts):
        self.proc = subprocess.Popen([NODE, "tests/js/mock_server.js", json.dumps(opts)], cwd=ROOT,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.sent = []

    def _roundtrip(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, "mock server died"
        return json.loads(line)

    async def connect(self):
        return True

    async def send_command(self, action, params=None):
        from src.client.websocket_client import MuseScoreClient

        self.sent.append((action, params or {}))
        return MuseScoreClient._unwrap(self._roundtrip({"action": action, "params": params or {}}))

    def mock(self, js):
        """Evaluates JS with `ms` (the MockMuseScore) in scope, e.g. a user edit."""
        return self._roundtrip({"__mock": js})["mock"]

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


@pytest.fixture
def mock_server():
    """(app, client): the MCP server wired to the plugin on the mock API."""
    if NODE is None:
        pytest.skip("node is not installed")
    import server as server_module

    clients = []

    def make(**opts):
        client = MockPluginClient(**opts)
        clients.append(client)
        return server_module.create_server(client), client

    yield make
    for c in clients:
        c.close()
