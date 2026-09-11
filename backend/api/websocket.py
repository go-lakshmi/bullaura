import asyncio
from fastapi import WebSocket, WebSocketDisconnect
from backend.services.app_state import state


async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    last_sent_at = None
    try:
        while True:
            snapshot = state.get_snapshot()
            snapshot_updated_at = snapshot.get("updated_at")

            # Do not resend the same snapshot every second. The backend state
            # is published by AppState on one synchronized 60-second cadence.
            if snapshot_updated_at != last_sent_at:
                await websocket.send_json(snapshot)
                last_sent_at = snapshot_updated_at

            await asyncio.sleep(1)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
