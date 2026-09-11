import time
from threading import Lock


class AppState:
    def __init__(self):
        self.engine = None
        self.lock = Lock()
        self.latest = {"updated_at": None, "stocks": []}
        self._last_published_at = 0.0
        self._publish_interval = 60.0

    def set_engine(self, engine):
        with self.lock:
            self.engine = engine

    def set_snapshot(self, snapshot):
        """Publish to the web UI at most once every 60 seconds."""
        now = time.monotonic()
        with self.lock:
            # Publish the first valid snapshot immediately, then enforce a
            # single 60-second cadence for every subsequent UI update.
            if self.latest.get("updated_at") is None or (now - self._last_published_at) >= self._publish_interval:
                self.latest = snapshot
                self._last_published_at = now
                return True
        return False

    def get_snapshot(self):
        with self.lock:
            return dict(self.latest)


# Shared application state used by FastAPI routes and the engine runner.
state = AppState()
