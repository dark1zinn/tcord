from typing import Any

from .glues import Auth, Channel, Relations
from .protocols import ClientProtocol


class TcordClient(ClientProtocol):
    """Compose presentation-facing services around a Rust-backed engine."""

    def __init__(self, engine: Any) -> None:
        self.auth = Auth(engine)
        self.relations = Relations(engine)
        self.channel = Channel(engine)
