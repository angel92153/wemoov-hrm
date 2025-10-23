import random
from .provider import HRProvider, HRReading

class SimHRProvider(HRProvider):
    def read_current(self, user_id: int) -> HRReading:
        bpm = random.randint(60, 150)
        return {"user_id": user_id, "bpm": bpm, "quality": "ok"}
