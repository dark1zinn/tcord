from typing import Protocol

from .auth import AuthProtocol
from .channel import ChannelProtocol
from .relations import RelationsProtocol


class ClientProtocol(Protocol):
    """Transport-agnostic client surface consumed by the TUI."""

    auth: AuthProtocol
    relations: RelationsProtocol
    channel: ChannelProtocol
