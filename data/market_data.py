from broker.angelone import AngelOneClient
from data.instrument_master import InstrumentMaster

from config.logger import logger as log


class MarketDataService:

    def __init__(
        self,
        broker: AngelOneClient,
        instrument_master: InstrumentMaster
    ):

        self.broker = broker
        self.instrument_master = instrument_master

    def get_ltp(
        self,
        symbol,
        exchange="NSE"
    ):

        instrument = self.instrument_master.get_instrument(
            symbol,
            exchange
        )

        if not instrument:

            raise Exception(
                f"Symbol not found: {symbol}"
            )

        token = instrument["token"]

        tradingsymbol = instrument["symbol"]

        response = self.broker.get_ltp(
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            symboltoken=token
        )

        return response

    def get_last_price(
        self,
        symbol,
        exchange="NSE"
    ):

        response = self.get_ltp(
            symbol,
            exchange
        )

        try:

            return response["data"]["ltp"]

        except Exception:

            return None