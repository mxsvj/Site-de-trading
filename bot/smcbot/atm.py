"""Modèle ATM : range de pré-séance, prise de liquidité, IFVG de confirmation.

Le déroulé décrit par le propriétaire, traduit sans y rien ajouter :

1. marquer le plus haut et le plus bas entre deux heures (défaut 13:00 → 15:25) ;
2. attendre que le prix aille chercher la liquidité d'un des deux bords ;
3. **écarter le trade si le dépassement excède `max_sweep_points`** — au-delà,
   ce n'est plus une prise de liquidité mais une sortie de range ;
4. attendre qu'un FVG laissé par cette poussée soit **inversé** (une bougie
   clôture de l'autre côté) : c'est la confirmation du retournement ;
5. entrer dans le sens du retournement, stop juste au-delà de l'extrême balayé ;
6. remonter le stop à l'entrée quand le prix atteint la **moitié du range**.

Deux choix d'implémentation méritent d'être explicites, parce qu'ils décident
du résultat bien plus que les seuils.

**L'entrée est au marché, à la clôture de la bougie qui inverse le FVG.** Le
modèle se joue traditionnellement sur le retour dans l'IFVG, donc à l'ordre
limite. Mais un ordre limite suppose un remplissage, et le moteur a montré
qu'accorder ce remplissage trop généreusement valait +0,022 R par trade — assez
pour changer le signe d'une stratégie. Entrer à la clôture ne suppose rien.
Tester la variante « retour dans l'IFVG » demande de faire varier
`--limit-margin` et de vérifier que le verdict tient.

**Un seul trade par jour.** Le modèle décrit une séquence quotidienne. Sans
cette limite, une journée agitée produirait plusieurs entrées corrélées qui
gonfleraient la statistique sans apporter d'observations indépendantes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from .config import BotConfig
from .data import Candle
from .smc import BEARISH, BULLISH
from .strategy import Signal


def _hhmm(texte: str) -> time:
    heures, minutes = texte.split(":")
    return time(int(heures), int(minutes))


@dataclass
class Ecart:
    """Un FVG en attente d'inversion.

    `bas` et `haut` bornent le déséquilibre ; `sens` est celui de la poussée qui
    l'a créé. Il devient un IFVG quand une bougie clôture du côté opposé.
    """

    sens: str
    bas: float
    haut: float
    age: int = 0


class AtmStrategy:
    """Range de pré-séance, balayage d'un bord, confirmation par IFVG.

    Paramètres (`strategy_params`) :
      range_start, range_end   bornes du range, dans le fuseau des bougies
      hunt_end                 heure au-delà de laquelle on n'entre plus
      max_sweep_points         dépassement maximal toléré du bord (défaut 40)
      stop_buffer_points       marge du stop au-delà de l'extrême balayé
      fvg_max_age              bougies au-delà desquelles un FVG est oublié
      min_fvg_points           taille minimale d'un FVG pour être retenu
      breakeven_at_mid         remonter le stop à l'entrée à mi-range
      tp_r                     objectif en multiples du risque
    """

    name = "atm"

    def __init__(self, cfg: BotConfig | None = None):
        self.cfg = cfg or BotConfig()
        p = self.cfg.strategy_params
        point = self.cfg.symbol.point

        self.range_start = _hhmm(str(p.get("range_start", "13:00")))
        self.range_end = _hhmm(str(p.get("range_end", "15:25")))
        self.hunt_end = _hhmm(str(p.get("hunt_end", "20:00")))
        self.max_sweep = float(p.get("max_sweep_points", 40.0)) * point
        self.stop_buffer = float(p.get("stop_buffer_points", 5.0)) * point
        self.fvg_max_age = int(p.get("fvg_max_age", 20))
        self.min_fvg = float(p.get("min_fvg_points", 0.0)) * point
        self.breakeven_at_mid = bool(p.get("breakeven_at_mid", True))
        self.tp_r = float(p.get("tp_r", self.cfg.risk.tp_r))

        self._jour: date | None = None
        self._reset_jour()
        self._precedentes: list[Candle] = []
        self._index = -1

    # ---------------------------------------------------------------- état

    def _reset_jour(self) -> None:
        self.haut = float("-inf")
        self.bas = float("inf")
        self.range_pret = False
        self.sens_balayage = ""
        """Sens de la poussée qui a pris la liquidité : BULLISH si le haut a
        été balayé — le trade attendu est alors baissier."""

        self.extreme_balaye = 0.0
        self.jour_disqualifie = False
        self.pris = False
        self.ecarts: list[Ecart] = []

    @property
    def milieu(self) -> float:
        return (self.haut + self.bas) / 2.0

    # -------------------------------------------------------------- signaux

    def on_candle(self, candle: Candle, can_open: bool = True) -> Signal | None:
        self._index += 1

        if self._jour != candle.time.date():
            self._jour = candle.time.date()
            self._reset_jour()

        signal = self._traiter(candle, can_open)

        self._precedentes.append(candle)
        if len(self._precedentes) > 3:
            self._precedentes.pop(0)
        return signal

    def _traiter(self, candle: Candle, can_open: bool) -> Signal | None:
        moment = candle.time.time()

        # 1. Construction du range.
        if self.range_start <= moment < self.range_end:
            self.haut = max(self.haut, candle.high)
            self.bas = min(self.bas, candle.low)
            return None

        if moment < self.range_start:
            return None
        if not self.range_pret:
            self.range_pret = self.haut > self.bas
        if not self.range_pret or self.pris or self.jour_disqualifie:
            return None
        if moment >= self.hunt_end:
            return None

        # 2. Prise de liquidité, et sa mesure.
        self._detecter_balayage(candle)
        if self.jour_disqualifie or not self.sens_balayage:
            self._suivre_ecarts(candle)
            return None

        # 3. Un FVG inversé confirme le retournement.
        inverse = self._chercher_inversion(candle)
        self._suivre_ecarts(candle)
        if inverse is None or not can_open:
            return None

        return self._construire_signal(candle, inverse)

    def _detecter_balayage(self, candle: Candle) -> None:
        """Repère le dépassement d'un bord, et disqualifie s'il est trop ample.

        Au-delà de `max_sweep_points`, le prix n'est pas allé chercher des stops
        pour repartir : il est sorti du range. Prendre le contrepied d'une vraie
        cassure est le principal mode d'échec de ce genre de modèle, d'où le
        filtre — et d'où le fait qu'il **disqualifie la journée** au lieu de
        simplement ignorer la bougie.
        """
        if self.sens_balayage:
            # Un balayage déjà en cours : on suit son extrême tant qu'il
            # s'étend, et on disqualifie s'il dépasse la limite.
            if self.sens_balayage == BULLISH:
                self.extreme_balaye = max(self.extreme_balaye, candle.high)
                if self.extreme_balaye - self.haut > self.max_sweep:
                    self.jour_disqualifie = True
            else:
                self.extreme_balaye = min(self.extreme_balaye, candle.low)
                if self.bas - self.extreme_balaye > self.max_sweep:
                    self.jour_disqualifie = True
            return

        if candle.high > self.haut:
            if candle.high - self.haut > self.max_sweep:
                self.jour_disqualifie = True
                return
            self.sens_balayage = BULLISH
            self.extreme_balaye = candle.high
        elif candle.low < self.bas:
            if self.bas - candle.low > self.max_sweep:
                self.jour_disqualifie = True
                return
            self.sens_balayage = BEARISH
            self.extreme_balaye = candle.low

    def _suivre_ecarts(self, candle: Candle) -> None:
        """Enregistre les FVG laissés par la poussée et fait vieillir les autres."""
        for ecart in self.ecarts:
            ecart.age += 1
        self.ecarts = [e for e in self.ecarts if e.age <= self.fvg_max_age]

        if len(self._precedentes) < 2:
            return
        avant = self._precedentes[-2]

        if candle.low > avant.high and candle.low - avant.high >= self.min_fvg:
            self.ecarts.append(Ecart(BULLISH, avant.high, candle.low))
        elif candle.high < avant.low and avant.low - candle.high >= self.min_fvg:
            self.ecarts.append(Ecart(BEARISH, candle.high, avant.low))

    def _chercher_inversion(self, candle: Candle) -> Ecart | None:
        """Un FVG de la poussée clôturé du côté opposé : le retournement est acté.

        On ne retient que les écarts créés **dans le sens du balayage**. Un FVG
        formé dans l'autre sens n'a rien à inverser — il appartient déjà au
        mouvement qu'on cherche.

        Les écarts laissés par la poussée *vers* la liquidité comptent : ce sont
        même les seuls que le modèle vise. Les effacer au moment du balayage
        ne laisserait que ceux formés après, donc trop tard.
        """
        attendu = BULLISH if self.sens_balayage == BULLISH else BEARISH
        for ecart in list(self.ecarts):
            if ecart.sens != attendu:
                continue
            if attendu == BULLISH and candle.close < ecart.bas:
                self.ecarts.remove(ecart)
                return ecart
            if attendu == BEARISH and candle.close > ecart.haut:
                self.ecarts.remove(ecart)
                return ecart
        return None

    def _construire_signal(self, candle: Candle, ecart: Ecart) -> Signal | None:
        renverse = BEARISH if self.sens_balayage == BULLISH else BULLISH

        if renverse == BEARISH:
            stop = self.extreme_balaye + self.stop_buffer
            if stop <= candle.close:
                return None
        else:
            stop = self.extreme_balaye - self.stop_buffer
            if stop >= candle.close:
                return None

        # Le breakeven du modèle est un niveau de marché : la moitié du range.
        # Il n'a de sens que devant l'entrée, sinon il se déclencherait aussitôt.
        milieu = self.milieu
        breakeven = None
        if self.breakeven_at_mid:
            devant = (
                milieu < candle.close if renverse == BEARISH else milieu > candle.close
            )
            breakeven = milieu if devant else None

        self.pris = True
        depassement = (
            self.extreme_balaye - self.haut
            if self.sens_balayage == BULLISH
            else self.bas - self.extreme_balaye
        )
        return Signal(
            index=self._index,
            time=candle.time,
            direction=renverse,
            entry_level=candle.close,
            stop=stop,
            tp_r=self.tp_r,
            entry_type="market",
            breakeven_price=breakeven,
            reason=(
                f"balayage {'haut' if self.sens_balayage == BULLISH else 'bas'} "
                f"de {depassement / self.cfg.symbol.point:.0f} pts, IFVG inverse"
            ),
        )
