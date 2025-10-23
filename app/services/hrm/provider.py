from abc import ABC, abstractmethod
from typing import TypedDict, Literal

class HRReading(TypedDict):
    user_id: int
    bpm: int
    quality: Literal["good", "ok", "bad"]

class HRProvider(ABC):
    @abstractmethod
    def read_current(self, user_id: int) -> HRReading:
        """Devuelve la lectura instantánea de HR para un usuario."""
        raise NotImplementedError
