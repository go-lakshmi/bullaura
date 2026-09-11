# Historical window helpers. The production historical-selection rules remain
# in nse_engine.py unchanged; these helpers are for the API/UI layer and tests.

def window(values, days):
    return list(values[:days])

def average(values):
    values = [float(v) for v in values if v is not None]
    return sum(values) / len(values) if values else 0.0
