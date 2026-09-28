# Compatibility facade; Swing and BTST now share the unified rating filter.
def evaluate(engine, symbol, past_data, live_metrics):
    return engine.evaluate_unified_candidate(symbol, past_data, live_metrics)
