import asyncio
import json
import threading
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from client import TcordClient
from tcord_core import Session


class _DiscordFixture(BaseHTTPRequestHandler):
    def _reply(self, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/auth/login":
            self._reply({"token": "test-token"})
            return
        content = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self._reply(
            {
                "id": "3",
                "channel_id": "10",
                "author": {"id": "1", "username": "me"},
                "content": content["content"],
                "timestamp": "2026-01-01T00:00:03+00:00",
            }
        )

    def do_GET(self) -> None:
        routes = {
            "/users/@me": {"id": "1", "username": "me"},
            "/users/@me/guilds": [{"id": "2", "name": "Guild"}],
            "/guilds/2/channels": [{"id": "10", "name": "general"}],
            "/users/@me/channels": [{"id": "20", "recipients": [{"username": "friend"}]}],
            "/channels/10/messages?limit=50": [
                {
                    "id": "2",
                    "channel_id": "10",
                    "author": {"id": "2", "username": "friend"},
                    "content": "later",
                    "timestamp": "2026-01-01T00:00:02+00:00",
                },
                {
                    "id": "1",
                    "channel_id": "10",
                    "author": {"id": "2", "username": "friend"},
                    "content": "earlier",
                    "timestamp": "2026-01-01T00:00:01+00:00",
                },
            ],
        }
        self._reply(routes[self.path])

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
async def client() -> AsyncIterator[TcordClient]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DiscordFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = TcordClient(Session(f"http://127.0.0.1:{server.server_port}"))
    await client.auth.login_with_password("person@example.test", "secret")
    for _ in range(100):
        if (await client.auth.auth_state()).kind == "authenticated":
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("fixture login did not authenticate")
    yield client
    await client.auth.logoff()
    server.shutdown()
    server.server_close()
    thread.join()


async def test_reads_and_sends_base_chat_data(client: TcordClient) -> None:
    assert (await client.relations.get_guilds())[0].name == "Guild"
    assert (await client.relations.get_channels("2"))[0].name == "general"
    assert (await client.relations.get_dms())[0].name == "friend"
    history = await client.channel.load_message_history("10")
    assert [message.id for message in history] == ["1", "2"]
    assert (await client.channel.send_message("10", "hello")).content == "hello"
