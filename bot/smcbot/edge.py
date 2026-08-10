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
    atr_moyen: float = 0.0
    """ATR moyen des observations de cette cellule, en prix.

    Indispensable pour juger la rentabilité : une cellule conditionnée sur la
    volatilité n'a pas l'ATR médian du marché. Lui appliquer le seuil global
    flatterait les cellules calmes, qui sont justement celles où le spread
    pèse le plus lourd."""

    moyenne_globale: float = 0.0
    """Rendement moyen inconditionnel, en multiples de l'ATR.

    Une cellule ne prouve quelque chose que si elle bat le marché sans
    condition, pas si elle se contente de le suivre. Mesuré le 2026-08-10 sur
    696 371 bougies M1 : à 60 bougies d'horizon, l'or dérivait de +22,7 points
    par fenêtre sur la période d'étude — l'or est passé de 2 524 à 4 119. Toutes
    les cellules ressortaient positives et l'une d'elles « couvrait ses frais »,
    alors qu'aucune n'apportait la moindre information : elles héritaient de la
    hausse. Le biais croît avec l'horizon, puisque la dérive est linéaire en
    temps."""

    def rentable_a_partir_de(self, spread_points: float, point: float) -> float:
        """Effet minimal, propre à cette cellule, pour couvrir le spread."""
        atr_points = self.atr_moyen / point if point > 0 else 0.0
        if atr_points <= 0:
            return float("inf")
        return spread_points / atr_points

    @property
    def exces(self) -> float:
        """Ce que la cellule apporte en plus du marché sans condition."""
        return self.moyenne - self.moyenne_globale

    def couvre_ses_frais(self, spread_points: float, point: float) -> bool:
        """Jugé sur l'excès : suivre la dérive ne coûte pas de condition.

        Prendre `moyenne` ici ferait passer pour rentable toute cellule d'un
        marché en tendance, y compris celles qui n'apportent rien.
        """
        return abs(self.exces) > self.rentable_a_partir_de(spread_points, point)

    @property
    def t(self) -> float:
        """Statistique sur l'excès, pas sur le rendement brut.

        Tester la moyenne contre zéro revient à demander « l'or a-t-il bougé ? »
        — vrai pour toutes les cellules d'un marché qui monte. La question utile
        est « cette condition dit-elle quelque chose de plus que l'absence de
        condition ? », donc l'écart à la moyenne globale.
        """
        if self.n < 2 or self.ecart_type <= 0:
            return 0.0
        return self.exces / (self.ecart_type / (self.n ** 0.5))

    @property
    def exploitable(self) -> bool:
        return self.n >= MIN_ECHANTILLON


@dataclass
class Balayage:
    cellules: list[Cellule] = field(default_factory=list)
    horizon: int = 15
    observations: int = 0
    atr_median: float = 0.0
    """ATR médian en points, pour rapporter les effets au spread."""

    moyenne_globale: float = 0.0
    """Rendement moyen sans aucune condition, en multiples de l'ATR."""

    @property
    def examinees(self) -> list[Cellule]:
        return [c for c in self.cellules if c.exploitable]

    @property
    def seuil(self) -> float:
        """Seuil de t exigé, corrigé du nombre de cellules examinées."""
        n = max(1, len(self.examinees))
        return NormalDist().inv_cdf(1.0 - ALPHA / (2.0 * n))

    def effet_detectable(self) -> float:
        """Plus petit effet que ce balayage aurait pu déclarer significatif.

        Un résultat nul ne vaut rien tant qu'on ignore ce qu'on aurait été
        capable de voir. Si la finesse de détection est plus grossière que
        l'effet recherché, « rien trouvé » signifie seulement « pas assez de
        données » — et n'autorise aucune conclusion sur le marché.

        Calculé sur la cellule la mieux fournie, donc la plus favorable.
        """
        candidates = [c for c in self.examinees if c.ecart_type > 0]
        if not candidates:
            return float("inf")
        meilleure = max(candidates, key=lambda c: c.n)
        return self.seuil * meilleure.ecart_type / (meilleure.n ** 0.5)

    def effet_rentable(self, spread_points: float, point: float) -> float:
        """Effet minimal pour qu'un trade couvre seulement son spread.

        Exprimé dans la même unité que les cellules — des multiples de l'ATR —
        pour être directement comparable à `effet_detectable`. L'ATR est
        stocké en prix et le spread en points : les confondre donnerait un
        seuil absurde de plusieurs dizaines d'ATR.
        """
        atr_points = self.atr_median / point if point > 0 else 0.0
        if atr_points <= 0:
            return float("inf")
        return spread_points / atr_points


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
    atrs_cellule: dict[tuple[str, str], list[float]] = {}
    tous_les_retours: list[float] = []
    observations = 0
    for i in indices:
        if atrs[i] <= 0:
            continue
        avant = candles[i].close
        if avant <= 0:
            continue
        futur = (candles[i + horizon].close - avant) / atrs[i]
        tous_les_retours.append(futur)
        observations += 1
        for nom, fonction in criteres.items():
            valeur = fonction(i)
            if valeur is not None:
                groupes.setdefault((nom, valeur), []).append(futur)
                atrs_cellule.setdefault((nom, valeur), []).append(atrs[i])

    # Référence inconditionnelle : ce que rapporte le fait de ne rien
    # conditionner. Une cellule n'apprend quelque chose que si elle s'en écarte.
    globale = (
        sum(tous_les_retours) / len(tous_les_retours) if tous_les_retours else 0.0
    )

    cellules = []
    for (critere, valeur), echantillon in groupes.items():
        n = len(echantillon)
        moyenne = sum(echantillon) / n
        if n > 1:
            variance = sum((x - moyenne) ** 2 for x in echantillon) / (n - 1)
        else:
            variance = 0.0
        propres = atrs_cellule.get((critere, valeur), [])
        cellules.append(
            Cellule(
                critere,
                valeur,
                n,
                moyenne,
                variance ** 0.5,
                atr_moyen=sum(propres) / len(propres) if propres else 0.0,
                moyenne_globale=globale,
            )
        )

    cellules.sort(key=lambda c: abs(c.t), reverse=True)
    positifs = sorted(a for a in atrs if a > 0)
    median = positifs[len(positifs) // 2] if positifs else 0.0
    return Balayage(
        cellules=cellules,
        horizon=horizon,
        observations=observations,
        atr_median=median,
        moyenne_globale=globale,
    )


@dataclass
class Confrontation:
    """Une cellule, mesurée sur deux périodes distinctes."""

    critere: str
    valeur: str
    dedans: Cellule
    dehors: Cellule

    @property
    def meme_sens(self) -> bool:
        return self.dedans.moyenne * self.dehors.moyenne > 0


def confronter(
    candles: Sequence[Candle], horizon: int = 15, split: float = 0.6
) -> tuple[list[Confrontation], float]:
    """Rejoue le balayage sur une période que la première n'a pas vue.

    Un balayage retient mécaniquement ses meilleures cellules : sur soixante
    conditions, les plus fortes le sont en partie par chance. La seule
    question qui tranche est de savoir si la même condition produit le même
    effet sur des données qui n'ont pas servi à la sélectionner.

    Une inversion de signe hors échantillon est le verdict le plus net qui
    soit : ce n'était pas un effet, c'était du bruit.
    """
    coupure = int(len(candles) * split)
    dedans = balayer(candles[:coupure], horizon=horizon)
    dehors = balayer(candles[coupure:], horizon=horizon)

    par_cle = {(c.critere, c.valeur): c for c in dehors.cellules}
    couples = []
    for cellule in dedans.examinees:
        jumelle = par_cle.get((cellule.critere, cellule.valeur))
        if jumelle is not None and jumelle.exploitable:
            couples.append(
                Confrontation(cellule.critere, cellule.valeur, cellule, jumelle)
            )
    couples.sort(key=lambda c: abs(c.dedans.t), reverse=True)
    return couples, dedans.seuil
