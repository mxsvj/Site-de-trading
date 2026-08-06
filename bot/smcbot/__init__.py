"""Bot de trading SMC pour MetaTrader 5 : backtest et paper trading.

Exemple minimal :

    from smcbot import BotConfig, run_backtest, synthetic_series

    candles = synthetic_series(3000)
    result = run_backtest(candles, BotConfig())
    print(result.report.to_text())
"""

from .backtest import BacktestResult, run_backtest
from .broker import PaperBroker, Position, Trade
from .config import BotConfig, RiskConfig, SmcConfig, SymbolSpec
from .data import Candle, Mt5Feed, ReplayFeed, load_csv, save_csv, synthetic_series
from .metrics import Report, build_report
from .paper import PaperTrader, setup_logging
from .smc import FairValueGap, OrderBlock, SmcEngine, StructureEvent, Swing
from .strategy import Signal, SmcStrategy

__version__ = "1.0.0"

__all__ = [
    "BacktestResult",
    "BotConfig",
    "Candle",
    "FairValueGap",
    "Mt5Feed",
    "OrderBlock",
    "PaperBroker",
    "PaperTrader",
    "Position",
    "Report",
    "ReplayFeed",
    "RiskConfig",
    "Signal",
    "SmcConfig",
    "SmcEngine",
    "SmcStrategy",
    "StructureEvent",
    "Swing",
    "SymbolSpec",
    "Trade",
    "build_report",
    "load_csv",
    "run_backtest",
    "save_csv",
    "setup_logging",
    "synthetic_series",
]
