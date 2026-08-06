"""Moteur de backtest bougie par bougie, sans accès aux données futures."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .broker import EquityPoint, PaperBroker, Trade
from .config import BotConfig
from .data import Candle
from .metrics import Report, build_report
from .strategy import SmcStrategy


@dataclass
class BacktestResult:
    report: Report
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    candles: int = 0
    signals: int = 0
    rejected: list[str] = field(default_factory=list)

    def save_trades(self, path: str | Path) -> None:
        """Exporte le journal des trades en CSV."""
        with Path(path).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "ouverture",
                    "cloture",
                    "sens",
                    "entree",
                    "stop",
                    "tp",
                    "sortie",
                    "lots",
                    "pnl",
                    "R",
                    "motif_sortie",
                    "setup",
                    "solde",
                ]
            )
            for t in self.trades:
                writer.writerow(
                    [
                        t.open_time.strftime("%Y-%m-%d %H:%M"),
                        t.close_time.strftime("%Y-%m-%d %H:%M"),
                        "achat" if t.direction == "bullish" else "vente",
                        f"{t.entry:.5f}",
                        f"{t.stop:.5f}",
                        f"{t.take_profit:.5f}",
                        f"{t.exit:.5f}",
                        t.lots,
                        f"{t.pnl:.2f}",
                        f"{t.r:.3f}",
                        t.exit_reason,
                        t.reason,
                        f"{t.balance_after:.2f}",
                    ]
                )

    def save_equity(self, path: str | Path) -> None:
        """Exporte la courbe de capital en CSV."""
        with Path(path).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["time", "balance", "equity"])
            for point in self.equity_curve:
                writer.writerow(
                    [
                        point.time.strftime("%Y-%m-%d %H:%M"),
                        f"{point.balance:.2f}",
                        f"{point.equity:.2f}",
                    ]
                )


def run_backtest(
    candles: Sequence[Candle], cfg: BotConfig | None = None
) -> BacktestResult:
    """Rejoue la série et applique la stratégie SMC.

    Chaque bougie est traitée dans cet ordre : gestion des positions ouvertes,
    puis mise à jour du moteur SMC, puis recherche d'une nouvelle entrée. Le
    moteur ne voit jamais une bougie postérieure à celle qu'il traite.
    """
    cfg = cfg or BotConfig()
    strategy = SmcStrategy(cfg)
    broker = PaperBroker(cfg)
    signals = 0

    for i, candle in enumerate(candles):
        broker.on_candle(candle, i)
        signal = strategy.on_candle(candle, can_open=broker.can_open)
        if signal is not None:
            signals += 1
            broker.execute(signal, candle, i)

    if candles and broker.positions:
        broker.close_all(candles[-1], len(candles) - 1, "fin de série")

    report = build_report(
        broker.trades, broker.equity_curve, cfg.risk.initial_balance
    )
    return BacktestResult(
        report=report,
        trades=broker.trades,
        equity_curve=broker.equity_curve,
        candles=len(candles),
        signals=signals,
        rejected=broker.rejected,
    )
