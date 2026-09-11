def percent_change(current, previous):
    previous = float(previous or 0)
    return ((float(current or 0) - previous) / previous) * 100.0 if previous else 0.0

def close_position(ltp, high, low):
    high, low = float(high or 0), float(low or 0)
    return ((float(ltp or 0) - low) / (high - low)) * 100.0 if high > low else 50.0
