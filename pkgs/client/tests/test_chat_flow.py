import json
import threading
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest
from client import TcordClient
from client.errors import AuthenticationError, InvalidInputError
from client.models import AuthKind, MessageReference
from tcord_core import Session

HISTORY = [
    {
        "id": "2",
        "channel_id": "10",
        "author": {"id": "2", "username": "friend", "global_name": "Friend"},
        "content": "later",
        "timestamp": "2026-01-01T00:00:02+00:00",
        "type": 19,
        "referenced_message": {
            "id": "1",
            "channel_id": "10",
            "author": {"id": "2", "username": "friend"},
            "content": "earlier",
            "timestamp": "2026-01-01T00:00:01+00:00",
        },
        "attachments": [{"filename": "a.png", "url": "https://cdn/a.png", "size": 10}],
    },
    {
        "id": "1",
        "channel_id": "10",
        "author": {"id": "2", "username": "friend"},
        "content": "earlier",
        "timestamp": "2026-01-01T00:00:01+00:00",
    },
]


class _DiscordFixture(BaseHTTPRequestHandler):
    sent: ClassVar[list[dict]] = []

    def _reply(self, value: object, status: int = 200) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/auth/login":
            self._reply({"token": "test-token"})
        elif self.path == "/channels/10/messages":
            type(self).sent.append(payload)
            self._reply(
                {
                    "id": "3",
                    "channel_id": "10",
                    "author": {"id": "1", "username": "me"},
                    "content": payload["content"],
                    "nonce": payload.get("nonce"),
                    "timestamp": "2026-01-01T00:00:03+00:00",
                }
            )
        else:
            self._reply({"message": "Unknown", "code": 0}, status=404)

    def do_GET(self) -> None:
        routes = {
            "/users/@me": {"id": "1", "username": "me"},
            "/users/@me/guilds": [{"id": "2", "name": "Guild"}],
            "/guilds/2/channels": [
                {"id": "9", "name": "Text", "type": 4},
                {"id": "10", "name": "general", "type": 0, "parent_id": "9"},
            ],
            "/users/@me/channels": [
                {"id": "20", "type": 1, "recipients": [{"id": "2", "username": "friend"}]}
            ],
            "/channels/10/messages?limit=50": HISTORY,
        }
        if self.path in routes:
            self._reply(routes[self.path])
        else:
            self._reply({"message": "Unknown", "code": 0}, status=404)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
async def client() -> AsyncIterator[TcordClient]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DiscordFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = Session(f"http://127.0.0.1:{server.server_port}", credential_store="memory")
    client = TcordClient(session)
    state = await client.auth.login_password("person@example.test", "secret")
    assert state.kind == AuthKind.AUTHENTICATED
    yield client
    await client.auth.logout()
    client.close()
    server.shutdown()
    server.server_close()
    thread.join()


async def test_reads_and_sends_base_chat_data(client: TcordClient) -> None:
    assert (await client.gateway.guilds())[0].name == "Guild"
    channels = await client.gateway.guild_channels("2")
    assert [(channel.name, channel.is_readable) for channel in channels] == [
        ("Text", False),
        ("general", True),
    ]
    dms = await client.gateway.private_channels()
    assert dms[0].is_private
    assert dms[0].recipients[0].username == "friend"

    history = await client.messages.history("10")
    assert [message.id for message in history] == ["1", "2"]
    reply = history[1]
    assert reply.author_name == "Friend"
    assert reply.referenced is not None
    assert reply.referenced.content == "earlier"
    assert reply.attachments[0].filename == "a.png"
    assert reply.timestamp.tzinfo is not None

    sent = await client.messages.send(
        "10", "hello", reply_to=MessageReference("2", "10"), nonce="42"
    )
    assert sent.content == "hello"
    assert sent.nonce == "42"
    assert _DiscordFixture.sent[-1]["message_reference"]["message_id"] == "2"


async def test_invalid_input_is_rejected_before_network(client: TcordClient) -> None:
    with pytest.raises(InvalidInputError):
        await client.messages.send("10", "   ")
    with pytest.raises(InvalidInputError):
        await client.messages.history("10", limit=0)


async def test_logout_revokes_rest_access(client: TcordClient) -> None:
    state = await client.auth.logout()
    assert state.kind == AuthKind.LOGGED_OUT
    with pytest.raises(AuthenticationError):
        await client.gateway.guilds()
