"""Stratégie SMC : biais donné par la structure, entrée sur retour en order block.

Le déroulé recherché est le schéma classique :

1. le prix balaie la liquidité d'un ancien extrême (sweep, optionnel) ;
2. il casse la structure dans l'autre sens (CHoCH puis BOS) — c'est le biais ;
3. la jambe impulsive laisse un déséquilibre (FVG) et un order block ;
4. on entre au retour dans cet order block, stop derrière la zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import BotConfig
from .data import TIMEFRAMES, Candle, Resampler
from .smc import BULLISH, OrderBlock, SmcEngine


@dataclass(slots=True)
class Signal:
    """Ordre à exécuter, exprimé en prix bid (l'exécution ajoute le spread)."""

    index: int
    time: datetime
    direction: str
    entry_level: float
    stop: float
    tp_r: float
    reason: str
    order_block: OrderBlock

    @property
    def risk_distance(self) -> float:
        return abs(self.entry_level - self.stop)


class SmcStrategy:
    """Traduit l'état du moteur SMC en signaux d'entrée.

    En mode multi-timeframe (`cfg.htf` renseigné), un second moteur tourne sur
    l'unité de temps supérieure : il fournit le biais, tandis que les entrées
    sont cherchées dans les order blocks de l'unité de temps courante. C'est le
    schéma indispensable en scalping, où la structure de la petite unité de
    temps, prise seule, n'est guère que du bruit.
    """

    def __init__(self, cfg: BotConfig | None = None):
        self.cfg = cfg or BotConfig()
        self.engine = SmcEngine(self.cfg.smc, self.cfg.symbol)

        self.htf_engine: SmcEngine | None = None
        self._resampler: Resampler | None = None
        if self.cfg.htf:
            minutes = TIMEFRAMES.get(self.cfg.htf)
            if minutes is None:
                raise ValueError(f"Unité de temps supérieure inconnue : {self.cfg.htf}")
            base = TIMEFRAMES.get(self.cfg.timeframe)
            if base is not None and minutes <= base:
                raise ValueError(
                    f"L'unité de temps supérieure ({self.cfg.htf}) doit être plus "
                    f"grande que celle des entrées ({self.cfg.timeframe})."
                )
            self.htf_engine = SmcEngine(self.cfg.htf_smc, self.cfg.symbol)
            self._resampler = Resampler(minutes)

    @property
    def bias(self) -> str | None:
        """Biais courant : celui de l'unité supérieure s'il y en a une."""
        if self.htf_engine is not None:
            return self.htf_engine.bias
        return self.engine.bias

    def on_candle(self, candle: Candle, can_open: bool = True) -> Signal | None:
        """Traite une bougie clôturée et renvoie un signal éventuel."""
        if self._resampler is not None and self.htf_engine is not None:
            # Seules les bougies supérieures *achevées* sont transmises.
            for htf_candle in self._resampler.push(candle):
                self.htf_engine.push(htf_candle)

        self.engine.push(candle)
        if not can_open:
            return None
        return self._find_entry(candle)

    # -------------------------------------------------------------- interne

    def _find_entry(self, candle: Candle) -> Signal | None:
        engine = self.engine
        bias = self.bias
        if bias is None:
            return None

        smc = self.cfg.smc
        buffer = self.cfg.risk.sl_buffer_points * self.cfg.symbol.point
        index = len(engine.candles) - 1

        # Les zones les plus récentes d'abord : ce sont les plus pertinentes.
        for ob in sorted(
            engine.live_order_blocks(bias), key=lambda z: z.created_at, reverse=True
        ):
            # Pas d'entrée sur la bougie de cassure elle-même : le retour dans la
            # zone doit se produire après.
            if ob.created_at >= index:
                continue
            if smc.require_fvg and not ob.has_fvg:
                continue
            if smc.require_sweep and not ob.swept:
                continue
            if smc.require_htf_zone and not self._in_htf_zone(ob):
                continue

            level = ob.mid if smc.entry_at_equilibrium else ob.entry_edge

            if ob.direction == BULLISH:
                if candle.low > level:
                    continue  # la zone n'a pas été atteinte sur cette bougie
                stop = ob.bottom - buffer
                if stop >= level:
                    continue
            else:
                if candle.high < level:
                    continue
                stop = ob.top + buffer
                if stop <= level:
                    continue

            ob.used = True
            return Signal(
                index=index,
                time=candle.time,
                direction=ob.direction,
                entry_level=level,
                stop=stop,
                tp_r=self.cfg.risk.tp_r,
                reason=self._describe(ob),
                order_block=ob,
            )

        return None

    def _in_htf_zone(self, ob: OrderBlock) -> bool:
        """La zone recoupe-t-elle un point d'intérêt de l'unité supérieure ?"""
        htf = self.htf_engine
        if htf is None:
            return True

        index = len(htf.candles) - 1
        for zone in htf.live_order_blocks(ob.direction):
            if ob.bottom <= zone.top and ob.top >= zone.bottom:
                return True

        for gap in htf.fvgs:
            if gap.filled or gap.direction != ob.direction:
                continue
            if index - gap.index > self.cfg.htf_smc.ob_max_age:
                continue
            if ob.bottom <= gap.top and ob.top >= gap.bottom:
                return True

        return False

    def _describe(self, ob: OrderBlock) -> str:
        parts = [f"OB {ob.direction} #{ob.index}"]
        if ob.has_fvg:
            parts.append("FVG")
        if ob.swept:
            parts.append("sweep")
        if self.htf_engine is not None:
            parts.append(f"biais {self.cfg.htf}")
        parts.append(f"cassure @ {ob.break_level:g}")
        return " + ".join(parts)
