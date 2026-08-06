"""Moteur SMC : swings, structure (BOS/CHoCH), order blocks, FVG, liquidité.

Le moteur est incrémental : on lui pousse les bougies une par une et il renvoie
les évènements détectés sur la bougie courante. Le backtest et le paper trading
partagent donc exactement le même code, et aucune information future n'est
accessible — un swing n'est confirmé qu'après `swing_lookback` bougies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import SmcConfig, SymbolSpec
from .data import Candle

BULLISH = "bullish"
BEARISH = "bearish"


@dataclass(slots=True)
class Swing:
    """Point pivot confirmé (fractale)."""

    index: int
    time: datetime
    price: float
    kind: str  # "high" ou "low"


@dataclass(slots=True)
class FairValueGap:
    """Déséquilibre à trois bougies."""

    index: int
    direction: str
    top: float
    bottom: float
    filled: bool = False

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


@dataclass(slots=True)
class OrderBlock:
    """Dernière bougie opposée avant l'impulsion qui casse la structure."""

    index: int
    direction: str
    top: float
    bottom: float
    created_at: int
    """Index de la bougie de cassure (l'OB devient valide à partir de là)."""

    break_level: float
    target: float
    has_fvg: bool = False
    swept: bool = False
    mitigated: bool = False
    invalidated: bool = False
    used: bool = False

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def entry_edge(self) -> float:
        """Bord proximal : le haut pour un OB haussier, le bas pour un baissier."""
        return self.top if self.direction == BULLISH else self.bottom

    def touches(self, candle: Candle) -> bool:
        """Le prix entre-t-il dans la zone sur cette bougie ?"""
        return candle.low <= self.top and candle.high >= self.bottom

    def is_live(self, index: int, max_age: int) -> bool:
        return (
            not self.invalidated
            and not self.used
            and (index - self.created_at) <= max_age
        )


@dataclass(slots=True)
class StructureEvent:
    """Cassure de structure : BOS (continuation) ou CHoCH (retournement)."""

    index: int
    time: datetime
    kind: str  # "BOS" ou "CHOCH"
    direction: str
    level: float


@dataclass(slots=True)
class SweepEvent:
    """Prise de liquidité : mèche au-delà d'un swing, clôture en deçà."""

    index: int
    time: datetime
    direction: str
    level: float


@dataclass(slots=True)
class Events:
    """Ce que le moteur a détecté sur la bougie courante."""

    structure: StructureEvent | None = None
    sweep: SweepEvent | None = None
    new_order_block: OrderBlock | None = None
    new_fvg: FairValueGap | None = None
    mitigated: list[OrderBlock] = field(default_factory=list)
    """Order blocks dans lesquels le prix vient de revenir — les signaux d'entrée."""


class SmcEngine:
    """État SMC du marché, mis à jour bougie par bougie."""

    def __init__(self, cfg: SmcConfig | None = None, symbol: SymbolSpec | None = None):
        self.cfg = cfg or SmcConfig()
        self.symbol = symbol or SymbolSpec()

        self.candles: list[Candle] = []
        self.swing_highs: list[Swing] = []
        self.swing_lows: list[Swing] = []

        # Niveaux de référence pour la détection de cassure. Remis à None une
        # fois cassés, jusqu'à confirmation d'un nouveau pivot.
        self.active_high: Swing | None = None
        self.active_low: Swing | None = None

        self.bias: str | None = None
        self.order_blocks: list[OrderBlock] = []
        self.fvgs: list[FairValueGap] = []
        self.structure_events: list[StructureEvent] = []
        self.last_sweep: SweepEvent | None = None

    # ------------------------------------------------------------------ API

    def push(self, candle: Candle) -> Events:
        """Traite une bougie clôturée et renvoie les évènements associés."""
        self.candles.append(candle)
        i = len(self.candles) - 1
        events = Events()

        self._confirm_swing(i)
        events.new_fvg = self._detect_fvg(i)
        events.sweep = self._detect_sweep(i)
        if events.sweep is not None:
            self.last_sweep = events.sweep

        events.structure = self._detect_structure_break(i)
        if events.structure is not None:
            events.new_order_block = self._build_order_block(events.structure, i)

        # La mitigation est évaluée en dernier : un order block créé sur la
        # bougie de cassure ne peut pas être « touché » par cette même bougie.
        events.mitigated = self._update_zones(i, skip=events.new_order_block)
        return events

    def prime(self, candles: list[Candle]) -> None:
        """Précharge un historique sans exploiter les évènements."""
        for candle in candles:
            self.push(candle)

    def live_order_blocks(self, direction: str | None = None) -> list[OrderBlock]:
        index = len(self.candles) - 1
        return [
            ob
            for ob in self.order_blocks
            if ob.is_live(index, self.cfg.ob_max_age)
            and (direction is None or ob.direction == direction)
        ]

    # -------------------------------------------------------------- interne

    def _confirm_swing(self, i: int) -> None:
        """Confirme la fractale située `swing_lookback` bougies en arrière."""
        n = self.cfg.swing_lookback
        j = i - n
        if j < n:
            return

        pivot = self.candles[j]

        # `>=` sur la gauche et `>` sur la droite pour départager les plateaux
        # sans confirmer deux fois le même sommet.
        left = self.candles[j - n : j]
        right = self.candles[j + 1 : j + n + 1]

        if all(pivot.high >= c.high for c in left) and all(
            pivot.high > c.high for c in right
        ):
            swing = Swing(j, pivot.time, pivot.high, "high")
            self.swing_highs.append(swing)
            self.active_high = swing

        if all(pivot.low <= c.low for c in left) and all(
            pivot.low < c.low for c in right
        ):
            swing = Swing(j, pivot.time, pivot.low, "low")
            self.swing_lows.append(swing)
            self.active_low = swing

    def _detect_fvg(self, i: int) -> FairValueGap | None:
        """FVG à trois bougies : écart entre la mèche de i-2 et celle de i."""
        if i < 2:
            return None
        a, _b, c = self.candles[i - 2], self.candles[i - 1], self.candles[i]
        min_size = self.cfg.fvg_min_points * self.symbol.point

        gap: FairValueGap | None = None
        if c.low > a.high and (c.low - a.high) > min_size:
            gap = FairValueGap(i, BULLISH, top=c.low, bottom=a.high)
        elif c.high < a.low and (a.low - c.high) > min_size:
            gap = FairValueGap(i, BEARISH, top=a.low, bottom=c.high)

        if gap is not None:
            self.fvgs.append(gap)
        return gap

    def _detect_sweep(self, i: int) -> SweepEvent | None:
        """Mèche au-delà d'un swing récent suivie d'une clôture en deçà."""
        c = self.candles[i]

        for swing in reversed(self.swing_lows[-5:]):
            if i - swing.index > self.cfg.sweep_lookback:
                break
            if c.low < swing.price <= c.close:
                return SweepEvent(i, c.time, BULLISH, swing.price)

        for swing in reversed(self.swing_highs[-5:]):
            if i - swing.index > self.cfg.sweep_lookback:
                break
            if c.high > swing.price >= c.close:
                return SweepEvent(i, c.time, BEARISH, swing.price)

        return None

    def _detect_structure_break(self, i: int) -> StructureEvent | None:
        """Cassure en clôture du dernier swing de référence."""
        c = self.candles[i]
        event: StructureEvent | None = None

        if self.active_high is not None and c.close > self.active_high.price:
            kind = "BOS" if self.bias == BULLISH else "CHOCH"
            event = StructureEvent(i, c.time, kind, BULLISH, self.active_high.price)
            self.active_high = None
            self.bias = BULLISH
        elif self.active_low is not None and c.close < self.active_low.price:
            kind = "BOS" if self.bias == BEARISH else "CHOCH"
            event = StructureEvent(i, c.time, kind, BEARISH, self.active_low.price)
            self.active_low = None
            self.bias = BEARISH

        if event is not None:
            self.structure_events.append(event)
        return event

    def _build_order_block(
        self, event: StructureEvent, i: int
    ) -> OrderBlock | None:
        """Remonte l'impulsion jusqu'à la dernière bougie de couleur opposée."""
        start = max(0, i - self.cfg.ob_lookback)
        ob_index: int | None = None

        for j in range(i, start - 1, -1):
            candle = self.candles[j]
            if event.direction == BULLISH and candle.bearish:
                ob_index = j
                break
            if event.direction == BEARISH and candle.bullish:
                ob_index = j
                break

        if ob_index is None:
            return None

        source = self.candles[ob_index]
        if self.cfg.ob_use_body:
            top, bottom = source.body_high, source.body_low
        else:
            top, bottom = source.high, source.low

        leg = self.candles[ob_index : i + 1]
        target = max(c.high for c in leg) if event.direction == BULLISH else min(
            c.low for c in leg
        )

        # Confluences : imbalance dans la jambe impulsive, liquidité prise juste
        # avant la cassure.
        has_fvg = any(
            f.direction == event.direction and ob_index <= f.index <= i
            for f in self.fvgs
        )
        swept = (
            self.last_sweep is not None
            and self.last_sweep.direction == event.direction
            and (i - self.last_sweep.index) <= self.cfg.sweep_lookback
        )

        ob = OrderBlock(
            index=ob_index,
            direction=event.direction,
            top=top,
            bottom=bottom,
            created_at=i,
            break_level=event.level,
            target=target,
            has_fvg=has_fvg,
            swept=swept,
        )
        self.order_blocks.append(ob)
        return ob

    def _update_zones(self, i: int, skip: OrderBlock | None) -> list[OrderBlock]:
        """Met à jour mitigation/invalidation des OB et remplissage des FVG."""
        c = self.candles[i]
        just_mitigated: list[OrderBlock] = []

        for ob in self.order_blocks:
            if ob.invalidated or ob.used or ob is skip or ob.created_at >= i:
                continue
            if ob.direction == BULLISH:
                if c.close < ob.bottom:
                    ob.invalidated = True
                    continue
            elif c.close > ob.top:
                ob.invalidated = True
                continue

            if not ob.mitigated and ob.touches(c):
                ob.mitigated = True
                just_mitigated.append(ob)

        for gap in self.fvgs:
            if gap.filled:
                continue
            if gap.direction == BULLISH and c.low <= gap.bottom:
                gap.filled = True
            elif gap.direction == BEARISH and c.high >= gap.top:
                gap.filled = True

        # Purge des zones trop anciennes pour éviter une croissance sans fin en
        # exécution continue.
        horizon = self.cfg.ob_max_age * 4
        if len(self.order_blocks) > 500:
            self.order_blocks = [
                ob for ob in self.order_blocks if i - ob.created_at <= horizon
            ]
        if len(self.fvgs) > 500:
            self.fvgs = [f for f in self.fvgs if i - f.index <= horizon]

        return just_mitigated
