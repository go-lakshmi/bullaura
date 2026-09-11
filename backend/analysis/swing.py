# Swing qualification facade. The supplied engine remains the source of truth.
def evaluate(engine, symbol, past_data, live_metrics):
    evaluator = getattr(engine, "evaluate_live_breakout", None)
    return evaluator(symbol, past_data, live_metrics) if evaluator else None
