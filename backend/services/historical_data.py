# Historical data entry point.
# The supplied NSE engine owns the production historical-data workflow.
def get_engine_historical(engine):
    return getattr(engine, "volume_shockers", {})
