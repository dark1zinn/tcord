from .protocols import ClientProtocol
from .glues import Auth, Relations, Messaging

class TcordClient(ClientProtocol):

    def __init__(self):
        # Instantiate your backend core here (e.g., the compiled PyO3 module)
        self._engine = None

        # Compose the feature classes, injecting the engine
        self.auth = Auth(self._engine)
        self.relations = Relations(self._engine)
        self.messaging = Messaging(self._engine)
