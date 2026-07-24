from typing import Protocol

from .auth import AuthProtocol
from .relations  import RelationsProtocol
from .messaging import MessagingProtocol

class ClientProtocol(Protocol):
    """The main governor contract."""
    auth: AuthProtocol
    relations: RelationsProtocol
    messaging: MessagingProtocol
