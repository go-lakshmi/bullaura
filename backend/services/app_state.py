import time
from threading import Lock


class AppState:
    def __init__(self):
        self.engine = None
        self.lock = Lock()
        self.latest = {"updated_at": None, "stocks": []}
        self._last_published_at = 0.0
        self._publish_interval = 60.0
        self._publish_count = 0

    def set_engine(self, engine):
        with self.lock:
            self.engine = engine

    def set_snapshot(self, snapshot, force=False):
        """Publish one completed batch to the local UI.

        The publisher thread is responsible for the 60-second cadence.
        `force=True` is used by that publisher so a slow snapshot build does
        not create a second timing gate.
        """
        now = time.monotonic()
        with self.lock:
            if force or (now - self._last_published_at) >= self._publish_interval:
                self.latest = snapshot
                self._last_published_at = now
                self._publish_count += 1
                return True
        return False

    def get_snapshot(self):
        with self.lock:
            return dict(self.latest)


# Shared application state used by FastAPI routes and the engine runner.
state = AppState()
