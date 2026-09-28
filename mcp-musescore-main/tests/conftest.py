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
