# Market-data service boundary.
# Angel One/NSE connections remain in the preserved production engine.
class MarketDataService:
    def __init__(self, engine):
        self.engine = engine
