# Historical data entry point.
# The supplied nse engine still owns the production NSE/yfinance workflow.
def get_engine_historical(engine):
    return getattr(engine, "volume_shockers", {})
