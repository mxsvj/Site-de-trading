"""Bot de trading SMC pour MetaTrader 5 : backtest et paper trading.

Exemple minimal :

    from smcbot import BotConfig, run_backtest, synthetic_series

    candles = synthetic_series(3000)
    result = run_backtest(candles, BotConfig())
    print(result.report.to_text())
"""

from .backtest import BacktestResult, run_backtest
from .broker import PaperBroker, Position, Trade
from .config import (
    PRESETS,
    BotConfig,
    FilterConfig,
    RiskConfig,
    SmcConfig,
    SymbolSpec,
    eurusd,
    scalping_xauusd,
    swing_eurusd,
    xauusd,
)
from .data import (
    Candle,
    Mt5Feed,
    ReplayFeed,
    Resampler,
    load_csv,
    resample,
    save_csv,
    synthetic_series,
)
from .filters import Rejection, TradeFilters, Window
from .metrics import Report, build_report
from .paper import PaperTrader, setup_logging
from .smc import FairValueGap, OrderBlock, SmcEngine, StructureEvent, Swing
from .strategy import Signal, SmcStrategy

__version__ = "1.0.0"

__all__ = [
    "PRESETS",
    "BacktestResult",
    "BotConfig",
    "Candle",
    "FairValueGap",
    "FilterConfig",
    "Mt5Feed",
    "OrderBlock",
    "PaperBroker",
    "PaperTrader",
    "Position",
    "Rejection",
    "Report",
    "ReplayFeed",
    "Resampler",
    "RiskConfig",
    "Signal",
    "SmcConfig",
    "SmcEngine",
    "SmcStrategy",
    "StructureEvent",
    "Swing",
    "SymbolSpec",
    "Trade",
    "TradeFilters",
    "Window",
    "build_report",
    "eurusd",
    "load_csv",
    "resample",
    "run_backtest",
    "save_csv",
    "scalping_xauusd",
    "setup_logging",
    "swing_eurusd",
    "synthetic_series",
    "xauusd",
]
