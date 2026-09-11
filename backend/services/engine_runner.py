"""Bootstrap the existing Angel One -> NSE pipeline for the web dashboard."""

import asyncio
import threading
import time

from backend.config.logger import logger as log
from backend.services.app_state import state

_engine_thread = None


def _run_original_pipeline():
    """Use the same startup sequence as the user's original main.py."""
    try:
        from backend.broker.angelone import AngelOneClient
        from data.instrument_master import InstrumentMaster
        from backend.services.nse_engine import NSEHighPerformanceTradingPipeline

        log.info("Starting up Stock Shocker / AI-Trader Pipeline...")

        # 1. Authenticate with Angel One exactly as the original main.py does.
        client = AngelOneClient()
        if not client.login():
            log.critical("Failed to authenticate with Angel One. Engine stopped.")
            return
        log.info("Angel One authentication successful.")

        # 2. Build the instrument master.
        master = InstrumentMaster()

        # 3. Construct the existing NSE pipeline. Its qualification logic is
        #    unchanged; the web UI only reads its resulting state.
        pipeline = NSEHighPerformanceTradingPipeline(client, master)
        state.set_engine(pipeline)

        # 4. Start Angel One websocket streaming.
        pipeline.start_websocket_stream()

        # 5. Allow the websocket to establish and synchronize subscriptions.
        log.info("Waiting for WebSocket framework token synchronization context...")
        time.sleep(5)

        # 6. Run the existing continuous funnel. This is the same long-running
        #    function used by the original application.
        log.info("Starting continuous NSE/Angel One funnel loop...")
        pipeline.run_continuous_funnel_loop()

    except ImportError as exc:
        log.critical(
            "Angel One bootstrap modules are missing from the project: %s. "
            "The folders 'broker/' and 'data/' from the original application "
            "must be present beside 'backend/'.",
            exc,
        )
    except Exception:
        log.exception("Stock Shocker engine failed during startup.")


def _snapshot_loop():
    """Build and publish exactly one local-UI batch every 60 seconds.

    IMPORTANT: do NOT build the 532-stock snapshot every second. That work
    was unnecessarily expensive and could delay the 60-second UI cadence.
    The engine keeps updating live state continuously; this thread takes one
    point-in-time snapshot of that state once per minute.
    """
    from backend.services.live_data import build_snapshot

    interval = 60.0
    next_publish = time.monotonic() + interval
    batch_no = 0

    while True:
        try:
            sleep_for = max(0.0, next_publish - time.monotonic())
            if sleep_for:
                time.sleep(sleep_for)

            engine = state.engine
            if engine is not None:
                started = time.monotonic()
                snapshot = build_snapshot(engine)
                build_seconds = time.monotonic() - started
                stocks = snapshot.get("stocks", [])
                batch_no += 1

                published = state.set_snapshot(snapshot, force=True)
                log.info(
                    "LOCAL UI 60s batch #%d | collected=%d | published=%s | build=%.2fs",
                    batch_no, len(stocks), published, build_seconds
                )

            # Keep the cadence anchored to the original schedule. If snapshot
            # construction takes longer than expected, skip missed boundaries
            # rather than sending several batches back-to-back.
            now = time.monotonic()
            while next_publish <= now:
                next_publish += interval

        except Exception:
            log.exception("Failed to build web dashboard snapshot.")
            next_publish = time.monotonic() + interval


async def start_engine(state_obj=state):
    global _engine_thread

    if _engine_thread and _engine_thread.is_alive():
        log.warning("Stock Shocker engine is already running; refusing a second instance.")
        return

    # Run the blocking broker/pipeline startup outside FastAPI's event loop.
    _engine_thread = threading.Thread(
        target=_run_original_pipeline,
        name="stock-shocker-engine",
        daemon=True,
    )
    _engine_thread.start()

    threading.Thread(
        target=_snapshot_loop,
        name="stock-shocker-ui-snapshot",
        daemon=True,
    ).start()
