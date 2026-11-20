from typing import Protocol

from .auth import AuthProtocol
from .gateway import GatewayProtocol
from .messages import MessagesProtocol


class ClientProtocol(Protocol):
    """Transport-agnostic client surface consumed by the TUI."""

    @property
    def auth(self) -> AuthProtocol: ...

    @property
    def gateway(self) -> GatewayProtocol: ...

    @property
    def messages(self) -> MessagesProtocol: ...

    def close(self) -> None: ...
