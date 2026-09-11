from typing import Any, Dict, List
from pydantic import BaseModel, Field

class StockView(BaseModel):
    symbol: str
    ltp: float = 0.0
    gain: float = 0.0
    rvol: float = 0.0
    buying_pressure: float = 0.5
    rating: float = 0.0
    btst_signal: str = ""
    swing_signal: str = ""
    recommendation: str = ""
    price_history: List[Dict[str, Any]] = Field(default_factory=list)
    volume_history: List[Dict[str, Any]] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
