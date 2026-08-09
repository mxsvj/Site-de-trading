"""Catalogue des stratégies disponibles.

Toutes exposent la même interface — `on_candle(candle, can_open) -> Signal | None`
— et sont donc interchangeables dans le backtest, le paper trading et le banc
d'essai. En ajouter une revient à écrire une classe et une ligne ici.
"""

from __future__ import annotations

from typing import Protocol

from .config import BotConfig
from .data import Candle
from .scalping import AsianSweepStrategy, FadeStrategy, OpeningRangeStrategy
from .strategy import Signal, SmcStrategy


class Strategy(Protocol):
    """Ce qu'une stratégie doit savoir faire."""

    name: str

    def on_candle(self, candle: Candle, can_open: bool = True) -> Signal | None:
        """Traite une bougie clôturée et renvoie un signal éventuel."""
        ...


STRATEGIES: dict[str, type] = {
    "smc": SmcStrategy,
    "asian-sweep": AsianSweepStrategy,
    "orb": OpeningRangeStrategy,
    "fade": FadeStrategy,
}


def make_strategy(cfg: BotConfig | None = None) -> Strategy:
    """Instancie la stratégie nommée dans la configuration."""
    cfg = cfg or BotConfig()
    classe = STRATEGIES.get(cfg.strategy)
    if classe is None:
        connues = ", ".join(sorted(STRATEGIES))
        raise ValueError(
            f"Stratégie inconnue : {cfg.strategy!r}. Disponibles : {connues}"
        )
    return classe(cfg)
