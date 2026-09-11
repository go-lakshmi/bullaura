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
