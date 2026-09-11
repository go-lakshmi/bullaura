def buying_pressure(buying_volume, selling_volume):
    total = float(buying_volume or 0) + float(selling_volume or 0)
    return float(buying_volume or 0) / total if total > 0 else 0.5

def selling_pressure(buying_volume, selling_volume):
    return 1.0 - buying_pressure(buying_volume, selling_volume)
