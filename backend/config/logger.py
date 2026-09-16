import logging
from datetime import datetime
from pathlib import Path


LOG_ROOT = Path("logs")
LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | "
    "PID=%(process)d | %(threadName)s | %(message)s"
)


class DailyFolderFileHandler(logging.Handler):
    """Write project logs to logs/YYYY-MM-DD/app.log.

    The file is selected from the current local date on every emit, so the
    first log after midnight automatically goes to the new day's file.
    No application restart is required for daily file rotation.
    """

    def __init__(self, root=LOG_ROOT):
        super().__init__()
        self.root = Path(root)
        self._date = None
        self._stream = None
        self._path = None

    def _ensure_stream(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._stream is not None and self._date == today:
            return

        if self._stream is not None:
            self._stream.flush()
            self._stream.close()

        directory = self.root / today
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / "app.log"
        self._stream = self._path.open("a", encoding="utf-8", buffering=1)
        self._date = today

    def emit(self, record):
        try:
            self.acquire()
            self._ensure_stream()
            message = self.format(record)
            self._stream.write(message + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)
        finally:
            self.release()

    def close(self):
        try:
            self.acquire()
            if self._stream is not None:
                self._stream.flush()
                self._stream.close()
                self._stream = None
        finally:
            self.release()
            super().close()


def _configure_logger():
    logger = logging.getLogger("stock_shocker")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Avoid duplicate handlers when modules are reloaded by uvicorn/dev mode.
    if getattr(logger, "_stock_shocker_configured", False):
        return logger

    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    daily_handler = DailyFolderFileHandler()
    daily_handler.setLevel(logging.INFO)
    daily_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(daily_handler)
    logger._stock_shocker_configured = True

    return logger


logger = _configure_logger()
