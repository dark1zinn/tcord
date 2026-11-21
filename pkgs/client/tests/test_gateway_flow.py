"""Live-chat flow through the real extension against in-process HTTP and gateway fakes."""

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest
import websockets
from client import TcordClient
from client.models import AuthKind, MessageCreated
from tcord_core import Session

SENT_NONCE = "1234"


def _message(message_id: str, content: str, nonce: str | None = None) -> dict:
    return {
        "id": message_id,
        "channel_id": "101",
        "guild_id": "100",
        "author": {"id": "1", "username": "me", "avatar": "hash"},
        "member": {"nick": "Me!", "roles": ["7"]},
        "content": content,
        "nonce": nonce,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "attachments": [
            {
                "id": "3",
                "filename": "cat.png",
                "url": "https://cdn.discordapp.com/attachments/1/2/cat.png",
                "size": 7,
                "content_type": "image/png",
                "width": 64,
                "height": 32,
            }
        ],
    }


class _Api(BaseHTTPRequestHandler):
    posts: ClassVar[list[dict]] = []

    def _reply(self, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/auth/login":
            self._reply({"token": "tok"})
        else:
            type(self).posts.append(payload)
            self._reply(_message("11", payload["content"], payload.get("nonce")))

    def do_GET(self) -> None:
        self._reply({"id": "1", "username": "me"})

    def log_message(self, format: str, *args: object) -> None:
        pass


async def _gateway(websocket: websockets.ServerConnection) -> None:
    await websocket.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 45000}}))
    async for raw in websocket:
        payload = json.loads(raw)
        if payload["op"] != 2:
            continue
        ready = {
            "user": {"id": "1", "username": "me"},
            "users": [{"id": "2", "username": "friend"}],
            "session_id": "S",
            "resume_gateway_url": "ws://unused",
            "guilds": [
                {
                    "id": "100",
                    "properties": {"name": "Guild"},
                    "channels": [{"id": "101", "type": 0, "name": "general"}],
                }
            ],
            "private_channels": [{"id": "200", "type": 1, "recipient_ids": ["2"]}],
            "read_state": {"entries": [], "partial": False, "version": 1},
        }
        await websocket.send(json.dumps({"op": 0, "s": 1, "t": "READY", "d": ready}))
        await websocket.send(
            json.dumps({"op": 0, "s": 2, "t": "MESSAGE_CREATE", "d": _message("10", "hello")})
        )
        # Echo of our own send, as Discord delivers it next to the REST response.
        await websocket.send(
            json.dumps(
                {"op": 0, "s": 3, "t": "MESSAGE_CREATE", "d": _message("11", "sent", SENT_NONCE)}
            )
        )


@pytest.fixture
async def client() -> AsyncIterator[TcordClient]:
    api = ThreadingHTTPServer(("127.0.0.1", 0), _Api)
    thread = threading.Thread(target=api.serve_forever, daemon=True)
    thread.start()
    async with websockets.serve(_gateway, "127.0.0.1", 0) as gateway:
        port = gateway.sockets[0].getsockname()[1]
        session = Session(
            f"http://127.0.0.1:{api.server_port}",
            credential_store="memory",
            gateway_url=f"ws://127.0.0.1:{port}/?v=9&encoding=json",
            reconnect_base=0.01,
        )
        client = TcordClient(session)
        assert (await client.auth.login_password("a@b.test", "pw")).kind == AuthKind.AUTHENTICATED
        yield client
        client.close()
    api.shutdown()
    api.server_close()
    thread.join()


async def _next_message(client: TcordClient) -> MessageCreated:
    while True:
        event = await asyncio.wait_for(client.gateway.next_event(), timeout=5)
        if isinstance(event, MessageCreated):
            return event


async def test_ready_live_events_send_and_logout(client: TcordClient) -> None:
    ready = await client.gateway.connect()
    assert ready.guilds[0].name == "Guild"
    assert ready.private_channels[0].recipients[0].username == "friend"

    incoming = await _next_message(client)
    assert incoming.message.content == "hello"
    assert incoming.message.author_name == "Me!"
    assert incoming.message.member is not None
    assert incoming.message.member.roles == ("7",)
    assert incoming.message.author.avatar == "hash"
    assert incoming.message.attachments[0].is_image

    sent = await client.messages.send("101", "sent", nonce=SENT_NONCE)
    echo = await _next_message(client)
    assert sent.id == echo.message.id == "11"
    assert echo.message.nonce == SENT_NONCE
    assert len(_Api.posts) == 1

    await client.auth.logout()
    assert await asyncio.wait_for(_drain(client), timeout=5) is None


async def _drain(client: TcordClient) -> None:
    while (event := await client.gateway.next_event()) is not None:
        del event
