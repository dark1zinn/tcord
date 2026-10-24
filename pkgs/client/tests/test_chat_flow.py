import asyncio
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
            "/users/@me/channels": [
                {"id": "20", "recipients": [{"username": "friend"}]}
            ],
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

    def log_message(self, *_: object) -> None:
        pass


class ChatFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _DiscordFixture)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = TcordClient(Session(f"http://127.0.0.1:{self.server.server_port}"))
        await self.client.auth.login_with_password("person@example.test", "secret")
        for _ in range(100):
            if (await self.client.auth.auth_state()).kind == "authenticated":
                return
            await asyncio.sleep(0.01)
        self.fail("fixture login did not authenticate")

    async def asyncTearDown(self) -> None:
        await self.client.auth.logoff()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    async def test_reads_and_sends_base_chat_data(self) -> None:
        self.assertEqual((await self.client.relations.get_guilds())[0].name, "Guild")
        self.assertEqual((await self.client.relations.get_channels("2"))[0].name, "general")
        self.assertEqual((await self.client.relations.get_dms())[0].name, "friend")
        self.assertEqual(
            [message.id for message in await self.client.channel.load_message_history("10")],
            ["1", "2"],
        )
        self.assertEqual(
            (await self.client.channel.send_message("10", "hello")).content,
            "hello",
        )
