from fastapi import APIRouter
from backend.services.app_state import state

router = APIRouter()

@router.get("/stocks")
async def get_stocks():
    return state.get_snapshot()

@router.get("/historical")
async def get_historical():
    engine = state.engine
    return getattr(engine, "volume_shockers", {}) if engine else {}


@router.get("/tick-health")
async def get_tick_health():
    engine = state.engine
    if engine is None:
        return {"status": "engine_not_running", "aggregate": {}, "queue_depth": 0, "stocks": {}}
    return engine.get_tick_processing_stats()
