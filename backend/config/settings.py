import os
from dotenv import load_dotenv
load_dotenv()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
UPDATE_INTERVAL_SECONDS = int(os.getenv("UPDATE_INTERVAL_SECONDS", "60"))
START_ENGINE = os.getenv("START_ENGINE", "0") == "1"
