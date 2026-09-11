def average_volume(volumes):
    values = [float(v) for v in volumes if v is not None]
    return sum(values) / len(values) if values else 0.0

def relative_volume(current_volume, average):
    average = float(average or 0)
    return float(current_volume or 0) / average if average > 0 else 0.0

def volume_surge(current_volume, baseline_volume):
    baseline = float(baseline_volume or 0)
    return ((float(current_volume or 0) - baseline) / baseline) if baseline > 0 else 0.0
