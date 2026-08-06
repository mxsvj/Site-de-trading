"""Filtres d'admissibilité : horaires, spread, coût relatif, plafond quotidien.

En swing trading ces filtres sont un confort. En scalping ils sont la moitié de
la stratégie : un stop de 60 points sur l'or avec 25 points de spread laisse
peu de place à l'erreur, et un trade pris à 3 h du matin la laisse encore moins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from .config import FilterConfig, SymbolSpec


@dataclass(frozen=True, slots=True)
class Window:
    """Plage horaire UTC. Une plage qui franchit minuit est gérée."""

    start: time
    end: time

    def contains(self, moment: time) -> bool:
        if self.start <= self.end:
            return self.start <= moment < self.end
        return moment >= self.start or moment < self.end  # à cheval sur minuit

    @classmethod
    def parse(cls, text: str) -> "Window":
        """Construit une plage à partir de "07:00-11:00"."""
        try:
            raw_start, raw_end = text.split("-")
            return cls(_parse_hhmm(raw_start), _parse_hhmm(raw_end))
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                f"Plage horaire invalide : {text!r} (format attendu : 07:00-11:00)"
            ) from exc


def _parse_hhmm(text: str) -> time:
    hours, minutes = text.strip().split(":")
    return time(int(hours), int(minutes))


class TradeFilters:
    """Évalue si un trade est admissible, et dit pourquoi il ne l'est pas."""

    def __init__(self, cfg: FilterConfig | None = None, symbol: SymbolSpec | None = None):
        self.cfg = cfg or FilterConfig()
        self.symbol = symbol or SymbolSpec()
        self.windows = [Window.parse(s) for s in self.cfg.sessions]

    # ------------------------------------------------------- filtres temporels

    def session_allows(self, when: datetime) -> bool:
        """Le moment est-il dans une session autorisée ?"""
        if self.cfg.weekdays and when.weekday() not in self.cfg.weekdays:
            return False
        if not self.windows:
            return True
        return any(w.contains(when.time()) for w in self.windows)

    # ---------------------------------------------------------- filtres de coût

    def cost_points(self, spread_points: float | None = None) -> float:
        """Frais totaux d'un aller-retour, exprimés en points.

        La commission est convertie en points via la valeur du point pour un
        lot, ce qui la rend comparable au spread et au risque du trade.
        """
        spread = (
            self.symbol.spread_points if spread_points is None else spread_points
        )
        commission = 0.0
        if self.symbol.value_per_point_per_lot > 0:
            commission = (
                self.symbol.commission_per_lot / self.symbol.value_per_point_per_lot
            )
        return spread + commission

    def cost_ratio(self, stop_points: float, spread_points: float | None = None) -> float:
        """Part du risque absorbée par les frais (0.30 = 30 %)."""
        if stop_points <= 0:
            return float("inf")
        return self.cost_points(spread_points) / stop_points

    def check_trade(
        self, stop_points: float, spread_points: float | None = None
    ) -> "Rejection | None":
        """Renvoie le motif de refus, ou None si le trade est admissible."""
        spread = (
            self.symbol.spread_points if spread_points is None else spread_points
        )

        if self.cfg.max_spread_points > 0 and spread > self.cfg.max_spread_points:
            return Rejection(
                "spread trop large",
                f"{spread:.0f} pts > {self.cfg.max_spread_points:.0f}",
            )

        if self.cfg.min_stop_points > 0 and stop_points < self.cfg.min_stop_points:
            return Rejection(
                "stop trop serré",
                f"{stop_points:.0f} pts < {self.cfg.min_stop_points:.0f}",
            )

        if self.cfg.max_cost_ratio > 0:
            ratio = self.cost_ratio(stop_points, spread)
            if ratio > self.cfg.max_cost_ratio:
                return Rejection(
                    "frais trop élevés au regard du risque",
                    f"{ratio:.0%} du risque, plafond {self.cfg.max_cost_ratio:.0%}",
                )

        return None


@dataclass(frozen=True, slots=True)
class Rejection:
    """Motif de refus d'un setup.

    `code` est stable (il sert à regrouper les statistiques), `detail` porte les
    valeurs chiffrées du cas précis.
    """

    code: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.code} ({self.detail})" if self.detail else self.code
