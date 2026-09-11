def rating_from_signal(signal):
    if not signal:
        return 0.0
    return float(signal.get("score", 0) or 0)
