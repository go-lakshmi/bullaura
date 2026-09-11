# Facade for the existing breakout engine. No qualification rules are changed.
def evaluate(engine, symbol, past_data, live_metrics):
    return engine.evaluate_live_breakout(symbol, past_data, live_metrics)
