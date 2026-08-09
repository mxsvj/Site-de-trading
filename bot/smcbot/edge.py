"""Balayage systématique : existe-t-il une condition prédictive, quelle qu'elle soit ?

Inventer des stratégies une par une explore l'espace des possibles par petits
trous d'épingle : sept familles testées ne disent rien des milliers d'autres.
Ce module renverse la démarche. Au lieu de demander « cette stratégie
gagne-t-elle ? », il demande :

    **Existe-t-il une condition observable sous laquelle le rendement futur de
    l'or n'est pas nul en moyenne ?**

C'est une question sur le marché, pas sur une stratégie. Elle se mesure sans
stop, sans objectif, sans spread — donc sans qu'aucun choix d'implémentation
ne puisse masquer ou fabriquer un résultat. Si aucune condition ne ressort,
aucune stratégie construite sur ces conditions ne peut fonctionner, et ce
n'est plus une conjecture.

Trois précautions décident de la validité du résultat.

**Échantillons disjoints.** Le rendement à 15 bougies de la bougie *i* et celui
de la bougie *i+1* partagent 14 bougies. Les traiter comme deux observations
indépendantes gonflerait la statistique t d'un facteur proche de racine de
l'horizon — assez pour faire ressortir du bruit comme significatif. On
n'échantillonne donc qu'une bougie sur `horizon`.

**Normalisation par la volatilité.** Un rendement de 100 points ne vaut pas la
même chose selon que l'ATR est à 80 ou à 400. Tout est exprimé en multiples de
l'ATR courant, sans quoi les cellules « heure calme » et « heure agitée »
seraient incomparables.

**Correction du multi-test.** Un balayage sur soixante cellules trouve
mécaniquement trois cellules « significatives » à 5 % même sur du bruit pur.
Le seuil est relevé à proportion du nombre de cellules réellement examinées.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Callable, Sequence

from .data import Candle

ALPHA = 0.05
MIN_ECHANTILLON = 30
"""En dessous, la moyenne d'une cellule est dominée par le hasard."""


@dataclass
class Cellule:
    """Une condition observable, et ce que l'or a fait ensuite."""

    critere: str
    valeur: str
    n: int
    moyenne: float
    """Rendement futur moyen, en multiples de l'ATR courant."""

    ecart_type: float

    @property
    def t(self) -> float:
        if self.n < 2 or self.ecart_type <= 0:
            return 0.0
        return self.moyenne / (self.ecart_type / (self.n ** 0.5))

    @property
    def exploitable(self) -> bool:
        return self.n >= MIN_ECHANTILLON


@dataclass
class Balayage:
    cellules: list[Cellule] = field(default_factory=list)
    horizon: int = 15
    observations: int = 0

    @property
    def examinees(self) -> list[Cellule]:
        return [c for c in self.cellules if c.exploitable]

    @property
    def seuil(self) -> float:
        """Seuil de t exigé, corrigé du nombre de cellules examinées."""
        n = max(1, len(self.examinees))
        return NormalDist().inv_cdf(1.0 - ALPHA / (2.0 * n))


def _quintile(valeur: float, bornes: Sequence[float]) -> str:
    for i, borne in enumerate(bornes):
        if valeur <= borne:
            return f"Q{i + 1}"
    return f"Q{len(bornes) + 1}"


def _bornes(valeurs: Sequence[float], parts: int = 5) -> list[float]:
    ordonnees = sorted(valeurs)
    if not ordonnees:
        return []
    return [
        ordonnees[int(len(ordonnees) * (i + 1) / parts) - 1]
        for i in range(parts - 1)
    ]


def criteres_standards(
    candles: Sequence[Candle], atrs: Sequence[float], retours: Sequence[float]
) -> dict[str, Callable[[int], str | None]]:
    """Conditions observables au moment de décider, jamais après.

    Chaque fonction ne lit que l'indice courant et ce qui le précède. C'est la
    même exigence que pour une stratégie : une condition qui utiliserait une
    bougie future produirait un résultat spectaculaire et faux.
    """
    bornes_atr = _bornes([a for a in atrs if a > 0])
    bornes_retour = _bornes([r for r in retours if r != 0.0])
    bornes_corps = _bornes(
        [
            abs(c.close - c.open) / (c.high - c.low)
            for c in candles
            if c.high > c.low
        ]
    )

    def heure(i: int) -> str:
        return f"{candles[i].time.hour:02d}h"

    def jour(i: int) -> str | None:
        noms = ("lundi", "mardi", "mercredi", "jeudi", "vendredi")
        j = candles[i].time.weekday()
        return noms[j] if j < len(noms) else None

    def volatilite(i: int) -> str | None:
        return _quintile(atrs[i], bornes_atr) if atrs[i] > 0 else None

    def momentum(i: int) -> str | None:
        return _quintile(retours[i], bornes_retour) if atrs[i] > 0 else None

    def corps(i: int) -> str | None:
        c = candles[i]
        if c.high <= c.low:
            return None
        return _quintile(abs(c.close - c.open) / (c.high - c.low), bornes_corps)

    def sens(i: int) -> str:
        c = candles[i]
        if c.close > c.open:
            return "haussière"
        if c.close < c.open:
            return "baissière"
        return "plate"

    return {
        "heure": heure,
        "jour": jour,
        "volatilité (ATR)": volatilite,
        "momentum récent": momentum,
        "corps de bougie": corps,
        "sens de la bougie": sens,
    }


def balayer(
    candles: Sequence[Candle],
    horizon: int = 15,
    atr_period: int = 14,
    lookback: int = 15,
) -> Balayage:
    """Mesure le rendement futur moyen sous chaque condition observable."""
    from .scalping import Atr

    if len(candles) < horizon + atr_period + lookback + 10:
        raise ValueError("Historique trop court pour ce balayage.")

    atr = Atr(atr_period)
    atrs: list[float] = []
    for c in candles:
        atr.push(c)
        atrs.append(atr.value)

    # Momentum : variation sur les `lookback` bougies précédentes, rapportée à
    # l'ATR pour rester comparable d'un régime de volatilité à l'autre.
    retours: list[float] = []
    for i, c in enumerate(candles):
        if i < lookback or atrs[i] <= 0:
            retours.append(0.0)
        else:
            retours.append((c.close - candles[i - lookback].close) / atrs[i])

    criteres = criteres_standards(candles, atrs, retours)

    # Échantillons disjoints : sans cela les fenêtres se chevauchent, les
    # observations sont corrélées, et le t est gonflé d'environ racine(horizon).
    debut = max(atr_period, lookback)
    indices = range(debut, len(candles) - horizon, horizon)

    groupes: dict[tuple[str, str], list[float]] = {}
    observations = 0
    for i in indices:
        if atrs[i] <= 0:
            continue
        avant = candles[i].close
        if avant <= 0:
            continue
        futur = (candles[i + horizon].close - avant) / atrs[i]
        observations += 1
        for nom, fonction in criteres.items():
            valeur = fonction(i)
            if valeur is not None:
                groupes.setdefault((nom, valeur), []).append(futur)

    cellules = []
    for (critere, valeur), echantillon in groupes.items():
        n = len(echantillon)
        moyenne = sum(echantillon) / n
        if n > 1:
            variance = sum((x - moyenne) ** 2 for x in echantillon) / (n - 1)
        else:
            variance = 0.0
        cellules.append(
            Cellule(critere, valeur, n, moyenne, variance ** 0.5)
        )

    cellules.sort(key=lambda c: abs(c.t), reverse=True)
    return Balayage(cellules=cellules, horizon=horizon, observations=observations)
