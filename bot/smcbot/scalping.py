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
from datetime import date, datetime, time, timezone

from .config import BotConfig
from .data import Candle
from .smc import BEARISH, BULLISH
from .strategy import Signal


def _cle_temps(moment: datetime) -> datetime:
    """Clé comparable entre deux séries, quelle que soit leur conscience du fuseau.

    Une série chargée avec fuseau et une autre sans ne se croiseraient jamais :
    tous les rapprochements échoueraient, la stratégie ne produirait aucun
    signal, et le silence passerait pour une absence de setups.
    """
    if moment.tzinfo is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


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


# -------------------------------------------------------------- vol-break


class VolBreakStrategy:
    """Cassure courte, mais uniquement quand la volatilité paie le spread.

    Toutes les stratégies précédentes choisissaient **où** entrer et subissaient
    le coût qui allait avec. Celle-ci choisit d'abord **quand** : le stop est
    proportionnel à l'ATR courant, donc la part du risque absorbée par le
    spread est constante par construction, et on n'ouvre que si cette part
    reste sous `max_cost`.

    Concrètement, sur l'or à 24 points de spread avec un stop d'un ATR et un
    plafond de coût à 12 %, il faut un ATR d'au moins 200 points pour qu'un
    trade soit seulement envisagé. Les heures calmes — où le spread représente
    la moitié du mouvement — sont écartées avant même de regarder le prix.

    C'est le seul levier qui reste après avoir mesuré que la forme du signal
    ne porte aucune information : agir sur le dénominateur du rapport
    frais / mouvement plutôt que sur le signal lui-même.

    Paramètres :
      atr_period       période de l'ATR (défaut 14)
      lookback         bougies dont on casse l'extrême (défaut 15)
      buffer_atr       marge de validation de la cassure, en ATR (défaut 0.10)
      stop_atr         distance du stop, en ATR (défaut 1.0)
      max_cost         part maximale du risque laissée au spread (défaut 0.12)
      max_stop_points  plafond de stop, 0 = aucun
      tp_r             take profit en R
    """

    name = "vol-break"

    def __init__(self, cfg: BotConfig | None = None):
        self.cfg = cfg or BotConfig()
        p = self.cfg.strategy_params
        self.atr = Atr(int(p.get("atr_period", 14)))
        self.lookback = int(p.get("lookback", 15))
        self.buffer_atr = float(p.get("buffer_atr", 0.10))
        self.stop_atr = float(p.get("stop_atr", 1.0))
        self.max_cost = float(p.get("max_cost", 0.12))
        self.min_atr_override = float(p.get("min_atr_points", 0.0))
        self.max_stop_points = float(p.get("max_stop_points", 0.0))
        self.tp_r = float(p.get("tp_r", self.cfg.risk.tp_r))

        self._hauts: list[float] = []
        self._bas: list[float] = []
        self._index = -1

    @property
    def atr_minimal(self) -> float:
        """ATR en dessous duquel le spread coûte trop cher, en points.

        Le stop vaut `stop_atr` × ATR ; le spread en représente
        `spread / (stop_atr × ATR)`. Exiger que ce rapport reste sous
        `max_cost` revient exactement à exiger cet ATR minimal.

        `min_atr_points` fige ce seuil au lieu de le déduire. C'est
        indispensable pour décomposer la perte : rejouer à spread nul
        ramènerait sinon le seuil à zéro, ouvrirait la porte à toutes les
        heures calmes, et comparerait deux populations de trades différentes
        au lieu de mesurer un coût.
        """
        if self.min_atr_override > 0:
            return self.min_atr_override
        if self.max_cost <= 0 or self.stop_atr <= 0:
            return 0.0
        return self.cfg.symbol.spread_points / (self.stop_atr * self.max_cost)

    def on_candle(self, candle: Candle, can_open: bool = True) -> Signal | None:
        self._index += 1
        self.atr.push(candle)

        hauts, bas = list(self._hauts), list(self._bas)
        self._hauts.append(candle.high)
        self._bas.append(candle.low)
        if len(self._hauts) > self.lookback:
            self._hauts.pop(0)
            self._bas.pop(0)

        atr = self.atr.value
        if not can_open or atr <= 0 or len(hauts) < self.lookback:
            return None

        point = self.cfg.symbol.point
        if atr / point < self.atr_minimal:
            return None

        distance = self.stop_atr * atr
        if self.max_stop_points > 0 and distance / point > self.max_stop_points:
            return None

        marge = self.buffer_atr * atr
        plafond, plancher = max(hauts), min(bas)

        if candle.close > plafond + marge:
            return Signal(
                index=self._index,
                time=candle.time,
                direction=BULLISH,
                entry_level=candle.close,
                stop=candle.close - distance,
                tp_r=self.tp_r,
                entry_type="market",
                reason=f"cassure haute, ATR {atr / point:.0f} pts",
            )

        if candle.close < plancher - marge:
            return Signal(
                index=self._index,
                time=candle.time,
                direction=BEARISH,
                entry_level=candle.close,
                stop=candle.close + distance,
                tp_r=self.tp_r,
                entry_type="market",
                reason=f"cassure basse, ATR {atr / point:.0f} pts",
            )

        return None


# --------------------------------------------------------------- lead-lag


class LeadLagStrategy:
    """Retard de l'or sur un marché plus liquide lié au dollar.

    Toutes les autres stratégies de ce dépôt lisent **le prix passé de l'or**,
    et cinq mesures indépendantes disent que cette source ne contient aucune
    information directionnelle. Celle-ci change de source.

    L'or est coté en dollars : quand le dollar se déprécie, l'or monte
    mécaniquement. Ce n'est pas une figure graphique, c'est de l'arithmétique
    de cotation. Or l'EUR/USD traite des volumes bien supérieurs à ceux de
    l'or : l'hypothèse est qu'il intègre une information sur le dollar
    **avant** l'or, et que le retard se rattrape en quelques minutes.

    On compare donc les deux variations récentes, chacune normalisée par sa
    propre volatilité — sans quoi on comparerait des pourcentages qui n'ont
    pas la même échelle. Quand la référence a bougé et pas l'or, on parie sur
    le rattrapage.

    Paramètres :
      reference_csv        série de référence (ex. data/eurusd_m1.csv)
      reference_tz_shift   décalage à lui appliquer, en heures
      correlation_sign     +1 si référence et or montent ensemble
      lookback             bougies sur lesquelles la variation est mesurée
      window               profondeur de la normalisation
      min_divergence       écart minimal, en écarts-types
      atr_period, stop_atr, max_cost, min_atr_points, tp_r  — comme vol-break
    """

    name = "lead-lag"

    def __init__(self, cfg: BotConfig | None = None):
        from .data import load_csv
        from .quality import shift_times

        self.cfg = cfg or BotConfig()
        p = self.cfg.strategy_params

        chemin = str(p.get("reference_csv", "")).strip()
        if not chemin:
            raise ValueError(
                "lead-lag exige une série de référence : "
                "--strategy-param reference_csv=data/eurusd_m1.csv"
            )
        reference = load_csv(chemin)
        decalage = float(p.get("reference_tz_shift", 0.0))
        if decalage:
            reference = shift_times(reference, decalage)
        self.reference = {_cle_temps(c.time): c.close for c in reference}
        if not self.reference:
            raise ValueError(f"Série de référence vide : {chemin}")

        self.signe = float(p.get("correlation_sign", 1.0))
        self.lookback = int(p.get("lookback", 5))
        self.window = int(p.get("window", 200))
        self.min_divergence = float(p.get("min_divergence", 1.5))

        self.atr = Atr(int(p.get("atr_period", 14)))
        self.stop_atr = float(p.get("stop_atr", 1.0))
        self.max_cost = float(p.get("max_cost", 0.12))
        self.min_atr_override = float(p.get("min_atr_points", 0.0))
        self.max_stop_points = float(p.get("max_stop_points", 0.0))
        self.tp_r = float(p.get("tp_r", self.cfg.risk.tp_r))

        self._or: list[float] = []
        self._ref: list[float] = []
        self._var_or: list[float] = []
        self._var_ref: list[float] = []
        self._index = -1

        self.consultations = 0
        self.manques = 0
        self._alerte_donnee = False

    @property
    def atr_minimal(self) -> float:
        if self.min_atr_override > 0:
            return self.min_atr_override
        if self.max_cost <= 0 or self.stop_atr <= 0:
            return 0.0
        return self.cfg.symbol.spread_points / (self.stop_atr * self.max_cost)

    def _z(self, valeurs: list[float]) -> float:
        """Dernière variation, en écarts-types de ses propres variations.

        Normaliser est indispensable : une variation de 0,1 % sur l'EUR/USD et
        0,1 % sur l'or ne représentent pas du tout le même évènement.
        """
        if len(valeurs) < self.window:
            return 0.0
        recent = valeurs[-self.window:]
        moyenne = sum(recent) / len(recent)
        variance = sum((v - moyenne) ** 2 for v in recent) / len(recent)
        ecart = variance ** 0.5
        # Une série quasi constante a un écart-type numériquement non nul mais
        # dénué de sens : le rapport exploserait et fabriquerait des signaux à
        # partir de bruit d'arrondi. Cas réel quand un marché est à l'arrêt.
        echelle = max((abs(v) for v in recent), default=0.0)
        if ecart <= 1e-12 or ecart <= 1e-9 * echelle:
            return 0.0
        return (valeurs[-1] - moyenne) / ecart

    # Deux séries peuvent légitimement ne pas se recouvrir au début : la
    # référence commence souvent plus tard que la série principale. Ce filet
    # ne se déclenche donc que sur un désalignement **total** et durable, seul
    # cas qu'un trou de tête ne peut pas expliquer. Le contrôle informatif du
    # recouvrement, lui, est fait en amont par la ligne de commande.
    JAMAIS_ALIGNE = 20_000

    def _prevenir_si_desaligne(self) -> None:
        """Signale le seul cas où aucun rapprochement n'a jamais abouti."""
        if self._alerte_donnee or self.consultations < self.JAMAIS_ALIGNE:
            return
        if self.manques == self.consultations:
            self._alerte_donnee = True
            print(
                f"⚠ lead-lag : aucun des {self.consultations} horodatages "
                f"n'a trouvé de correspondance.\n"
                f"  Les deux séries ne sont pas alignées — décalage horaire "
                f"différent, ou unités de temps différentes.\n"
                f"  Tout résultat obtenu ainsi serait vide de sens."
            )

    def on_candle(self, candle: Candle, can_open: bool = True) -> Signal | None:
        self._index += 1
        self.atr.push(candle)

        self.consultations += 1
        ref = self.reference.get(_cle_temps(candle.time))
        if ref is None:
            self.manques += 1
            self._prevenir_si_desaligne()
            return None

        self._or.append(candle.close)
        self._ref.append(ref)
        if len(self._or) > self.lookback + 1:
            self._or.pop(0)
            self._ref.pop(0)
        if len(self._or) <= self.lookback:
            return None

        # Variations relatives sur la fenêtre, pour que les deux séries soient
        # comparables malgré des niveaux de prix sans commune mesure.
        if self._or[0] <= 0 or self._ref[0] <= 0:
            return None
        self._var_or.append((self._or[-1] - self._or[0]) / self._or[0])
        self._var_ref.append((self._ref[-1] - self._ref[0]) / self._ref[0])
        if len(self._var_or) > self.window:
            self._var_or.pop(0)
            self._var_ref.pop(0)

        atr = self.atr.value
        if not can_open or atr <= 0:
            return None

        point = self.cfg.symbol.point
        if atr / point < self.atr_minimal:
            return None

        distance = self.stop_atr * atr
        if self.max_stop_points > 0 and distance / point > self.max_stop_points:
            return None

        divergence = self.signe * self._z(self._var_ref) - self._z(self._var_or)

        if divergence >= self.min_divergence:
            return Signal(
                index=self._index,
                time=candle.time,
                direction=BULLISH,
                entry_level=candle.close,
                stop=candle.close - distance,
                tp_r=self.tp_r,
                entry_type="market",
                reason=f"or en retard de {divergence:.1f} ecarts-types",
            )
        if divergence <= -self.min_divergence:
            return Signal(
                index=self._index,
                time=candle.time,
                direction=BEARISH,
                entry_level=candle.close,
                stop=candle.close + distance,
                tp_r=self.tp_r,
                entry_type="market",
                reason=f"or en avance de {abs(divergence):.1f} ecarts-types",
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
