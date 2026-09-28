# Compatibility facade; the unified rating engine also owns BREAK status.
def evaluate(engine, symbol, past_data, live_metrics):
    return engine.evaluate_unified_candidate(symbol, past_data, live_metrics)
