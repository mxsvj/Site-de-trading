"""Stratégies de scalping, alternatives au schéma SMC.

Chacune expose la même interface que `SmcStrategy` : `on_candle(candle, can_open)`
renvoie un `Signal` ou None. Elles sont donc interchangeables dans le backtest,
le paper trading et le banc d'essai.

Elles partagent une contrainte que le scalping rend impitoyable : le spread est
fixe, donc un stop serré le paie proportionnellement plus cher. Chacune expose
la distance de son stop pour que les filtres de coût puissent trancher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from .config import BotConfig
from .data import Candle
from .smc import BEARISH, BULLISH
from .strategy import Signal


def _parse_hhmm(texte: str) -> time:
    heures, minutes = texte.split(":")
    return time(int(heures), int(minutes))


def _dans(moment: time, debut: time, fin: time) -> bool:
    if debut <= fin:
        return debut <= moment < fin
    return moment >= debut or moment < fin  # à cheval sur minuit


# --------------------------------------------------------------------- outils


@dataclass(slots=True)
class Plage:
    """Extrêmes d'une plage horaire en cours de constitution."""

    haut: float = float("-inf")
    bas: float = float("inf")
    bougies: int = 0

    def ajouter(self, candle: Candle) -> None:
        self.haut = max(self.haut, candle.high)
        self.bas = min(self.bas, candle.low)
        self.bougies += 1

    @property
    def prete(self) -> bool:
        return self.bougies > 0 and self.haut > self.bas


class Atr:
    """ATR incrémental (moyenne mobile simple des true ranges)."""

    def __init__(self, periode: int = 14):
        self.periode = periode
        self._valeurs: list[float] = []
        self._precedent: Candle | None = None

    def push(self, candle: Candle) -> None:
        if self._precedent is None:
            tr = candle.high - candle.low
        else:
            tr = max(
                candle.high - candle.low,
                abs(candle.high - self._precedent.close),
                abs(candle.low - self._precedent.close),
            )
        self._valeurs.append(tr)
        if len(self._valeurs) > self.periode:
            self._valeurs.pop(0)
        self._precedent = candle

    @property
    def value(self) -> float:
        if len(self._valeurs) < self.periode:
            return 0.0
        return sum(self._valeurs) / len(self._valeurs)


class Ema:
    """Moyenne mobile exponentielle incrémentale."""

    def __init__(self, periode: int = 20):
        self.periode = periode
        self._k = 2.0 / (periode + 1)
        self.value = 0.0
        self._n = 0

    def push(self, prix: float) -> None:
        self._n += 1
        if self._n == 1:
            self.value = prix
        else:
            self.value = prix * self._k + self.value * (1 - self._k)

    @property
    def prete(self) -> bool:
        return self._n >= self.periode


# ------------------------------------------------------------ asian-sweep


class AsianSweepStrategy:
    """Balayage de la plage asiatique à l'ouverture de Londres.

    Le schéma le plus répandu du scalping sur l'or : la séance asiatique
    construit une plage étroite, l'ouverture de Londres va chercher les stops
    d'un côté, puis repart dans l'autre sens. On entre sur la réintégration.

    Paramètres (`strategy_params`) :
      asian_start, asian_end  plage asiatique en UTC (défaut 00:00 → 06:00)
      hunt_start, hunt_end    fenêtre de chasse (défaut 07:00 → 11:00)
      buffer_points           marge du stop sous l'extrême balayé
      tp_r                    take profit en R (défaut : celui de la config)
    """

    name = "asian-sweep"

    def __init__(self, cfg: BotConfig | None = None):
        self.cfg = cfg or BotConfig()
        p = self.cfg.strategy_params
        self.asian_start = _parse_hhmm(str(p.get("asian_start", "00:00")))
        self.asian_end = _parse_hhmm(str(p.get("asian_end", "06:00")))
        self.hunt_start = _parse_hhmm(str(p.get("hunt_start", "07:00")))
        self.hunt_end = _parse_hhmm(str(p.get("hunt_end", "11:00")))
        self.buffer = float(p.get("buffer_points", 30.0)) * self.cfg.symbol.point
        self.tp_r = float(p.get("tp_r", self.cfg.risk.tp_r))

        self.plage = Plage()
        self._jour: date | None = None
        self._pris: set[str] = set()
        self._index = -1

    def on_candle(self, candle: Candle, can_open: bool = True) -> Signal | None:
        self._index += 1
        moment = candle.time.time()

        if self._jour != candle.time.date():
            self._jour = candle.time.date()
            self.plage = Plage()
            self._pris.clear()

        if _dans(moment, self.asian_start, self.asian_end):
            self.plage.ajouter(candle)
            return None

        if not can_open or not self.plage.prete:
            return None
        if not _dans(moment, self.hunt_start, self.hunt_end):
            return None

        # Balayage du bas puis réintégration : les vendeurs stoppés alimentent
        # la remontée.
        if (
            "long" not in self._pris
            and candle.low < self.plage.bas
            and candle.close > self.plage.bas
        ):
            self._pris.add("long")
            stop = candle.low - self.buffer
            if stop < candle.close:
                return Signal(
                    index=self._index,
                    time=candle.time,
                    direction=BULLISH,
                    entry_level=candle.close,
                    stop=stop,
                    tp_r=self.tp_r,
                    entry_type="market",
                    reason=f"balayage bas asiatique {self.plage.bas:g}",
                )

        if (
            "short" not in self._pris
            and candle.high > self.plage.haut
            and candle.close < self.plage.haut
        ):
            self._pris.add("short")
            stop = candle.high + self.buffer
            if stop > candle.close:
                return Signal(
                    index=self._index,
                    time=candle.time,
                    direction=BEARISH,
                    entry_level=candle.close,
                    stop=stop,
                    tp_r=self.tp_r,
                    entry_type="market",
                    reason=f"balayage haut asiatique {self.plage.haut:g}",
                )

        return None


# -------------------------------------------------------------------- orb


class OpeningRangeStrategy:
    """Cassure de la plage d'ouverture (opening range breakout).

    On mesure les N premières minutes d'une séance, puis on trade la sortie de
    cette plage, stop de l'autre côté.

    Paramètres :
      session_start   début de séance en UTC (défaut 07:00)
      range_minutes   durée de la plage d'ouverture (défaut 60)
      hunt_minutes    durée pendant laquelle la cassure est acceptée (défaut 240)
      buffer_points   marge au-delà de la plage pour valider la cassure
    """

    name = "orb"

    def __init__(self, cfg: BotConfig | None = None):
        self.cfg = cfg or BotConfig()
        p = self.cfg.strategy_params
        self.session_start = _parse_hhmm(str(p.get("session_start", "07:00")))
        self.range_minutes = int(p.get("range_minutes", 60))
        self.hunt_minutes = int(p.get("hunt_minutes", 240))
        self.buffer = float(p.get("buffer_points", 10.0)) * self.cfg.symbol.point
        self.tp_r = float(p.get("tp_r", self.cfg.risk.tp_r))

        self.plage = Plage()
        self._jour: date | None = None
        self._pris = False
        self._index = -1

    def _minutes_depuis_ouverture(self, moment: datetime) -> float:
        debut = moment.replace(
            hour=self.session_start.hour,
            minute=self.session_start.minute,
            second=0,
            microsecond=0,
        )
        return (moment - debut).total_seconds() / 60.0

    def on_candle(self, candle: Candle, can_open: bool = True) -> Signal | None:
        self._index += 1

        if self._jour != candle.time.date():
            self._jour = candle.time.date()
            self.plage = Plage()
            self._pris = False

        ecoule = self._minutes_depuis_ouverture(candle.time)
        if ecoule < 0:
            return None
        if ecoule < self.range_minutes:
            self.plage.ajouter(candle)
            return None

        if self._pris or not can_open or not self.plage.prete:
            return None
        if ecoule > self.range_minutes + self.hunt_minutes:
            return None

        hauteur = self.plage.haut - self.plage.bas
        if hauteur <= 0:
            return None

        if candle.close > self.plage.haut + self.buffer:
            self._pris = True
            return Signal(
                index=self._index,
                time=candle.time,
                direction=BULLISH,
                entry_level=candle.close,
                stop=self.plage.bas,
                tp_r=self.tp_r,
                entry_type="market",
                reason=f"cassure haute de la plage d'ouverture ({hauteur:g})",
            )

        if candle.close < self.plage.bas - self.buffer:
            self._pris = True
            return Signal(
                index=self._index,
                time=candle.time,
                direction=BEARISH,
                entry_level=candle.close,
                stop=self.plage.haut,
                tp_r=self.tp_r,
                entry_type="market",
                reason=f"cassure basse de la plage d'ouverture ({hauteur:g})",
            )

        return None


# ------------------------------------------------------------------- fade


class FadeStrategy:
    """Retour à la moyenne : on prend le contrepied d'une extension.

    Quand le prix s'écarte de plus de `atr_mult` ATR de sa moyenne mobile, on
    parie sur le retour. C'est le schéma de scalping le plus courant après la
    cassure — et celui qui souffre le plus des tendances soutenues.

    Paramètres :
      ema_period, atr_period, atr_mult, stop_atr_mult
    """

    name = "fade"

    def __init__(self, cfg: BotConfig | None = None):
        self.cfg = cfg or BotConfig()
        p = self.cfg.strategy_params
        self.ema = Ema(int(p.get("ema_period", 20)))
        self.atr = Atr(int(p.get("atr_period", 14)))
        self.atr_mult = float(p.get("atr_mult", 2.0))
        self.stop_atr_mult = float(p.get("stop_atr_mult", 1.5))
        self.tp_r = float(p.get("tp_r", self.cfg.risk.tp_r))
        self._index = -1

    def on_candle(self, candle: Candle, can_open: bool = True) -> Signal | None:
        self._index += 1
        self.atr.push(candle)
        self.ema.push(candle.close)

        atr = self.atr.value
        if not can_open or atr <= 0 or not self.ema.prete:
            return None

        ecart = candle.close - self.ema.value
        seuil = self.atr_mult * atr
        marge = self.stop_atr_mult * atr

        if ecart > seuil:
            return Signal(
                index=self._index,
                time=candle.time,
                direction=BEARISH,
                entry_level=candle.close,
                stop=candle.close + marge,
                tp_r=self.tp_r,
                entry_type="market",
                reason=f"extension haussière {ecart / atr:.1f} ATR",
            )
        if ecart < -seuil:
            return Signal(
                index=self._index,
                time=candle.time,
                direction=BULLISH,
                entry_level=candle.close,
                stop=candle.close - marge,
                tp_r=self.tp_r,
                entry_type="market",
                reason=f"extension baissière {abs(ecart) / atr:.1f} ATR",
            )
        return None
