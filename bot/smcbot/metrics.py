"""Statistiques de performance calculées sur les trades et la courbe de capital."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Sequence

from .broker import EquityPoint, Trade
from .risk import rollovers


@dataclass
class Report:
    """Synthèse d'un backtest ou d'une session de paper trading."""

    trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    winrate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    max_consecutive_losses: int = 0
    initial_balance: float = 0.0
    final_balance: float = 0.0
    return_pct: float = 0.0
    sharpe: float = 0.0
    long_trades: int = 0
    short_trades: int = 0
    median_hold_hours: float = 0.0
    max_hold_hours: float = 0.0
    overnight_pct: float = 0.0
    """Part des trades conservés au-delà d'un rollover — donc soumis au portage."""
    weekend_pct: float = 0.0
    """Part des trades conservés par-dessus un week-end, exposés au gap du dimanche."""
    total_swap: float = 0.0
    exit_reasons: dict[str, int] = field(default_factory=dict)

    def to_text(self) -> str:
        """Rendu console lisible."""
        lines = [
            "┌─ Résultats ────────────────────────────────",
            f"│ Trades              : {self.trades}  "
            f"({self.long_trades} long / {self.short_trades} short)",
            f"│ Winrate             : {self.winrate:.1f} %  "
            f"({self.wins}G / {self.losses}P / {self.breakeven}N)",
            f"│ Profit factor       : {self._fmt(self.profit_factor)}",
            f"│ Espérance           : {self.expectancy_r:+.3f} R par trade",
            f"│ Gain moyen          : {self.avg_win:+.2f}",
            f"│ Perte moyenne       : {self.avg_loss:+.2f}",
            f"│ Meilleur / pire     : {self.best_trade:+.2f} / {self.worst_trade:+.2f}",
            f"│ Pertes consécutives : {self.max_consecutive_losses}",
            "├─ Détention ────────────────────────────────",
            f"│ Durée médiane       : {self._duree(self.median_hold_hours)}  "
            f"(max {self._duree(self.max_hold_hours)})",
            f"│ Gardés la nuit      : {self.overnight_pct:.0f} %  "
            f"(week-end : {self.weekend_pct:.0f} %)",
            f"│ Portage total       : {self.total_swap:+.2f}",
            "├─ Capital ──────────────────────────────────",
            f"│ Départ              : {self.initial_balance:,.2f}",
            f"│ Final               : {self.final_balance:,.2f}",
            f"│ Performance         : {self.return_pct:+.2f} %",
            f"│ Drawdown max        : {self.max_drawdown:,.2f} "
            f"({self.max_drawdown_pct:.2f} %)",
            f"│ Sharpe (par trade)  : {self.sharpe:.2f}",
            "└────────────────────────────────────────────",
        ]
        if self.exit_reasons:
            detail = ", ".join(
                f"{k}: {v}" for k, v in sorted(self.exit_reasons.items())
            )
            lines.append(f"Sorties : {detail}")
        return "\n".join(lines)

    @staticmethod
    def _duree(heures: float) -> str:
        if heures < 1:
            return f"{heures * 60:.0f} min"
        if heures < 48:
            return f"{heures:.1f} h"
        return f"{heures / 24:.1f} j"

    @staticmethod
    def _fmt(value: float) -> str:
        return "∞" if math.isinf(value) else f"{value:.2f}"


def build_report(
    trades: Sequence[Trade],
    equity_curve: Sequence[EquityPoint],
    initial_balance: float,
) -> Report:
    """Agrège les trades et la courbe de capital en un rapport."""
    report = Report(
        initial_balance=initial_balance,
        final_balance=equity_curve[-1].balance if equity_curve else initial_balance,
    )
    if trades:
        report.final_balance = trades[-1].balance_after

    report.trades = len(trades)
    report.long_trades = sum(1 for t in trades if t.direction == "bullish")
    report.short_trades = report.trades - report.long_trades

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    report.wins = len(wins)
    report.losses = len(losses)
    report.breakeven = report.trades - report.wins - report.losses
    report.winrate = 100.0 * report.wins / report.trades if report.trades else 0.0

    report.gross_profit = sum(t.pnl for t in wins)
    report.gross_loss = abs(sum(t.pnl for t in losses))
    report.net_profit = report.gross_profit - report.gross_loss
    report.profit_factor = (
        report.gross_profit / report.gross_loss
        if report.gross_loss > 0
        else (math.inf if report.gross_profit > 0 else 0.0)
    )

    report.avg_win = report.gross_profit / len(wins) if wins else 0.0
    report.avg_loss = -report.gross_loss / len(losses) if losses else 0.0
    report.best_trade = max((t.pnl for t in trades), default=0.0)
    report.worst_trade = min((t.pnl for t in trades), default=0.0)

    r_values = [t.r for t in trades]
    report.expectancy_r = sum(r_values) / len(r_values) if r_values else 0.0
    report.sharpe = _sharpe(r_values)

    report.max_consecutive_losses = _max_streak(trades)
    report.max_drawdown, report.max_drawdown_pct = _drawdown(equity_curve)

    report.return_pct = (
        (report.final_balance - initial_balance) / initial_balance * 100.0
        if initial_balance
        else 0.0
    )

    for trade in trades:
        report.exit_reasons[trade.exit_reason] = (
            report.exit_reasons.get(trade.exit_reason, 0) + 1
        )

    _add_durations(report, trades)
    return report


def _add_durations(report: Report, trades: Sequence[Trade]) -> None:
    """Durées de détention : c'est ce qui décide du style réel de la stratégie.

    Une stratégie qui garde ses positions plusieurs jours n'est pas du scalping,
    quels que soient les paramètres affichés — et elle paie un portage et
    s'expose aux gaps du week-end, que le nom qu'on lui donne n'écarte pas.
    """
    if not trades:
        return

    durees = [
        (t.close_time - t.open_time).total_seconds() / 3600.0 for t in trades
    ]
    report.median_hold_hours = statistics.median(durees)
    report.max_hold_hours = max(durees)
    report.total_swap = sum(t.swap for t in trades)

    nuits = sum(1 for t in trades if rollovers(t.open_time, t.close_time) > 0)
    report.overnight_pct = 100.0 * nuits / len(trades)

    weekends = sum(
        1
        for t in trades
        if any(
            (t.open_time + timedelta(days=d)).weekday() == 5
            and t.open_time + timedelta(days=d) <= t.close_time
            for d in range(0, (t.close_time - t.open_time).days + 1)
        )
    )
    report.weekend_pct = 100.0 * weekends / len(trades)


def _sharpe(r_values: Sequence[float]) -> float:
    """Sharpe calculé sur la série des résultats en R (sans taux sans risque)."""
    if len(r_values) < 2:
        return 0.0
    mean = sum(r_values) / len(r_values)
    variance = sum((r - mean) ** 2 for r in r_values) / (len(r_values) - 1)
    std = math.sqrt(variance)
    # Une dispersion numériquement nulle — tous les trades au même R — donne un
    # t infini, qui n'informe sur rien. On le neutralise plutôt que d'afficher
    # un nombre astronomique dont la seule lecture possible est « bug ».
    if std <= max(1e-12, abs(mean) * 1e-9):
        return 0.0
    return mean / std * math.sqrt(len(r_values))


def _max_streak(trades: Sequence[Trade]) -> int:
    streak = worst = 0
    for trade in trades:
        if trade.pnl < 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return worst


def _drawdown(curve: Sequence[EquityPoint]) -> tuple[float, float]:
    """Drawdown maximal en valeur absolue et en pourcentage du pic."""
    peak = -math.inf
    max_dd = 0.0
    max_dd_pct = 0.0
    for point in curve:
        peak = max(peak, point.equity)
        if peak <= 0:
            continue
        drop = peak - point.equity
        if drop > max_dd:
            max_dd = drop
        pct = drop / peak * 100.0
        if pct > max_dd_pct:
            max_dd_pct = pct
    return max_dd, max_dd_pct
