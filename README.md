# Stock Shocker

Modular local-first dashboard around the existing NSE / Angel One stock engine.

## Important strategy-preservation rule

The supplied `nse(20260910-094607).py` is preserved as `backend/services/nse_engine.py` with its calculation and qualification logic intact. The older supplied versions and mobile dashboard are also kept under `legacy_backup/`.

The modular folders provide clear boundaries for API, market data, historical data, live data and analysis. The existing production engine remains the source of truth until each calculation is extracted and regression-tested.

## One-click Windows setup

1. Install Python 3.11+ and Node.js LTS.
2. Double-click `install.bat`.
3. Double-click `start.bat`.
4. The dashboard opens at `http://127.0.0.1:5173`.

## Engine startup

The dashboard starts safely with `START_ENGINE=0`, because the original application requires the same broker/instrument bootstrap used by your current production program.

After wiring your existing broker factory into `backend/services/engine_runner.py`, set:

    START_ENGINE=1
    STOCK_SHOCKER_ENGINE_FACTORY=your_module:create_engine

This intentionally keeps broker credentials out of the frontend.

## Architecture

    Angel One / NSE / yfinance
            |
            v
    existing production engine
            |
            v
    modular services + analysis
            |
            +---- REST API
            |
            +---- WebSocket (60 sec)
            |
            v
         React UI

## Backup

`legacy_backup/` contains the supplied source versions so nothing is lost during the refactor.
