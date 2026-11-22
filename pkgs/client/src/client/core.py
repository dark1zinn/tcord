import tcord_core

from .glues import Auth, Gateway, Media, Messages
from .protocols import ClientProtocol


class TcordClient(ClientProtocol):
    """Compose presentation-facing services around one native engine session."""

    def __init__(self, engine: tcord_core.Session) -> None:
        self._engine = engine
        self._auth = Auth(engine)
        self._gateway = Gateway(engine)
        self._messages = Messages(engine)
        self._media = Media(engine)

    @property
    def auth(self) -> Auth:
        return self._auth

    @property
    def gateway(self) -> Gateway:
        return self._gateway

    @property
    def messages(self) -> Messages:
        return self._messages

    @property
    def media(self) -> Media:
        return self._media

    def close(self) -> None:
        self._engine.close()
