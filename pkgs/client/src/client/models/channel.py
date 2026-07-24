from dataclasses import dataclass

@dataclass
class Channel:
    id: str
    name: str
    is_dm: bool
