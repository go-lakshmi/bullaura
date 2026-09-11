import asyncio
from fastapi import WebSocket, WebSocketDisconnect
from backend.services.app_state import state
from backend.config.logger import logger as log


async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client = getattr(websocket, "client", None)
    log.info("LOCAL UI WebSocket connected: %s", client)
    last_sent_at = None
    try:
        while True:
            snapshot = state.get_snapshot()
            snapshot_updated_at = snapshot.get("updated_at")

            # A snapshot is published by the backend exactly once per 60-second
            # batch. Send it once per connected browser client.
            if snapshot_updated_at is not None and snapshot_updated_at != last_sent_at:
                await websocket.send_json(snapshot)
                last_sent_at = snapshot_updated_at
                log.info(
                    "LOCAL UI WebSocket sent batch updated_at=%s stocks=%d client=%s",
                    snapshot_updated_at, len(snapshot.get("stocks", [])), client
                )

            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, asyncio.CancelledError):
        log.info("LOCAL UI WebSocket disconnected: %s", client)
        return
    except Exception:
        log.exception("LOCAL UI WebSocket failed: %s", client)
        return
