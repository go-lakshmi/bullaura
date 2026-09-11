# Existing BTST qualification is intentionally delegated to the preserved engine.
def evaluate(engine, symbol, past_data, live_metrics):
    return engine.evaluate_btst_candidate(symbol, past_data, live_metrics)
