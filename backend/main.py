from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from backend.api.stocks import router as stocks_router
from backend.api.websocket import websocket_endpoint
from backend.services.app_state import state
from backend.config import settings

app = FastAPI(title="Stock Shocker", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks_router, prefix="/api")


@app.websocket("/ws/stocks")
async def stocks_ws(websocket: WebSocket):
    await websocket_endpoint(websocket)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "engine_running": state.engine is not None,
    }


@app.on_event("startup")
async def startup():
    if not settings.START_ENGINE:
        return

    from backend.services.engine_runner import start_engine
    await start_engine(state)


@app.on_event("shutdown")
async def shutdown():
    pass
