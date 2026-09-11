from SmartApi import SmartConnect
from dotenv import load_dotenv
from backend.config.logger import logger as log

import pyotp
import os

load_dotenv()


class AngelOneClient:

    def __init__(self):

        self.api_key = os.getenv("ANGEL_API_KEY")
        self.client_id = os.getenv("ANGEL_CLIENT_ID")
        self.password = os.getenv("ANGEL_PASSWORD")
        self.totp_secret = os.getenv("ANGEL_TOTP_SECRET")

        self.smart_api = None
        self.session = None
        self.refresh_token = None
        self.jwt_token = None

    def login(self):

        try:
            self.smart_api = SmartConnect(api_key=self.api_key)

            totp = pyotp.TOTP(self.totp_secret).now()

            self.session = self.smart_api.generateSession(
                self.client_id,
                self.password,
                totp
            )

            self.refresh_token = self.session["data"]["refreshToken"]
            self.jwt_token = self.session["data"]["jwtToken"]

            log.info("Angel One Login Successful")

            return True

        except Exception as ex:
            log.exception(f"Login Failed: {ex}")
            return False

    def get_profile(self):

        try:
            
            return self.smart_api.getProfile(
                self.refresh_token
            )

        except Exception as ex:
            log.exception(f"Get Profile Failed: {ex}")
            return None

    def get_funds(self):

        try:
            return self.smart_api.rmsLimit()

        except Exception as ex:
            log.exception(f"Get Funds Failed: {ex}")
            return None

    def get_holdings(self):

        try:
            return self.smart_api.holding()

        except Exception as ex:
            log.exception(f"Get Holdings Failed: {ex}")
            return None

    def get_positions(self):

        try:
            return self.smart_api.position()

        except Exception as ex:
            log.exception(f"Get Positions Failed: {ex}")
            return None

    def get_ltp(
        self,
        exchange,
        tradingsymbol,
        symboltoken
    ):

        try:
            return self.smart_api.ltpData(
                exchange,
                tradingsymbol,
                symboltoken
            )

        except Exception as ex:
            log.exception(f"Get LTP Failed: {ex}")
            return None

    # Append these methods inside your existing AngelOneClient class in broker/angelone.py

    def place_order(self, variety, trading_symbol, symbol_token, exchange, transaction_type, order_type, product_type, quantity, price=0, trigger_price=0):
        """Places a buy or sell order using the SmartAPI REST interface"""
        try:
            order_params = {
                "variety": variety,            # "NORMAL", "STOPLOSS", etc.
                "tradingsymbol": trading_symbol,
                "symboltoken": symbol_token,
                "exchange": exchange,          # "NSE", "BSE", "NFO"
                "transactiontype": transaction_type,  # "BUY", "SELL"
                "ordertype": order_type,        # "MARKET", "LIMIT"
                "producttype": product_type,    # "INTRADAY", "DELIVERY", "CARRYFORWARD"
                "duration": "DAY",
                "quantity": str(quantity),
                "price": str(price),
                "triggerprice": str(trigger_price)
            }
            response = self.smart_api.placeOrder(order_params)
            if response and response.get("status"):
                log.info(f"Order placed successfully! Order ID: {response['data']['orderid']}")
                return response["data"]["orderid"]
            else:
                log.error(f"Order rejected by broker: {response.get('message')}")
                return None
        except Exception as ex:
            log.exception(f"Order execution crashed: {ex}")
            return None

    def get_order_book(self):
        """Fetches status of all orders placed today"""
        try:
            return self.smart_api.orderBook()
        except Exception as ex:
            log.exception(f"Order book fetch failed: {ex}")
            return None
    
    def logout(self):

        try:

            if self.smart_api:
                self.smart_api.terminateSession(
                    self.client_id
                )

            log.info("Session Terminated")

        except Exception as ex:
            log.exception(f"Logout Failed: {ex}")
