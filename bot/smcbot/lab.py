"""Banc d'essai : comparer des stratégies sans se mentir sur le résultat.

Tester des stratégies jusqu'à en trouver une qui marche a un coût statistique
que presque personne ne compte. En cherchant assez, on trouve **toujours** :
sur 100 hypothèses sans le moindre avantage, environ 5 franchissent le seuil
usuel de significativité par pur hasard. Le gagnant d'une longue recherche est
donc, par défaut, du bruit — à moins de relever la barre à proportion du nombre
d'essais.

Ce module fait trois choses :

1. il exécute chaque candidate dans le même pipeline honnête (séparation
   apprentissage/validation, préchauffe, garde-fou d'échantillon) ;
2. il **compte les hypothèses testées, y compris celles des sessions
   précédentes** — chercher en dix fois ne coûte pas moins cher qu'en une ;
3. il applique une correction de Bonferroni au seuil de significativité, et
   dit franchement si le meilleur résultat la franchit ou non.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Sequence

from .backtest import run_backtest
from .config import BotConfig
from .data import Candle
from .metrics import Report

# Seuil de significativité avant correction, et minimum de trades exploitables.
ALPHA = 0.05
MIN_TRADES = 30


@dataclass
class Essai:
    """Une candidate évaluée sur les deux périodes."""

    strategy: str
    label: str
    params: dict = field(default_factory=dict)
    dedans: Report = field(default_factory=Report)
    dehors: Report = field(default_factory=Report)
    refus: dict[str, int] = field(default_factory=dict)
    """Setups détectés puis écartés — filtres de coût, volume, horaires."""

    @property
    def exploitable(self) -> bool:
        return (
            self.dedans.trades >= MIN_TRADES and self.dehors.trades >= MIN_TRADES
        )

    @property
    def refuses(self) -> int:
        return sum(self.refus.values())

    @property
    def motif_dominant(self) -> tuple[str, int] | None:
        """Motif de refus le plus fréquent, s'il y en a."""
        if not self.refus:
            return None
        return max(self.refus.items(), key=lambda kv: kv[1])

    @property
    def etouffe(self) -> bool:
        """La stratégie a-t-elle été bâillonnée plutôt que muette ?

        Un candidat qui déclenche 300 fois et se voit refuser 296 entrées n'a
        pas « peu de signaux » : il en a beaucoup, et le compte les rejette.
        Confondre les deux ferait conclure sur une stratégie qui n'a jamais été
        évaluée.
        """
        total = self.dedans.trades + self.refuses
        return total > 0 and self.refuses > total / 2

    @property
    def t_validation(self) -> float:
        """Statistique t de l'espérance hors échantillon.

        `Report.sharpe` vaut déjà moyenne / écart-type × racine(n), c'est-à-dire
        exactement le t de Student de l'espérance par trade.
        """
        return self.dehors.sharpe


class Journal:
    """Compte les hypothèses testées, d'une session à l'autre.

    Sans persistance, on pourrait tester vingt stratégies par jour pendant un
    mois et juger la meilleure comme si elle était la première : c'est le moyen
    le plus sûr de prendre du bruit pour une découverte.
    """

    def __init__(self, chemin: str | Path | None = None):
        self.chemin = Path(chemin) if chemin else None
        self.herite = 0
        """Hypothèses d'avant le suivi par empreinte, non déduplicables."""

        self.empreintes: set[str] = set()
        self.sessions: list[dict] = []
        self.rejouees = 0
        """Hypothèses de la dernière session déjà vues à l'identique."""

        if self.chemin and self.chemin.exists():
            self._charger()

    @property
    def total(self) -> int:
        return self.herite + len(self.empreintes)

    def _charger(self) -> None:
        assert self.chemin is not None
        try:
            brut = json.loads(self.chemin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.sessions = list(brut.get("sessions", []))
        self.empreintes = set(brut.get("empreintes", []))
        if "empreintes" in brut:
            self.herite = int(brut.get("herite", 0))
        else:
            # Journal écrit avant le suivi par empreinte : son total ne peut
            # pas être dédupliqué rétroactivement, faute de savoir ce qu'il
            # comptait. On le conserve tel quel plutôt que de l'effacer — le
            # sous-estimer serait plus grave que le surestimer.
            self.herite = int(brut.get("total", 0))

    def enregistrer(self, essais: Sequence["Essai"], detail: str) -> int:
        """Ajoute les hypothèses réellement nouvelles. Renvoie leur nombre."""
        nouvelles = 0
        for essai in essais:
            signature = empreinte(essai)
            if signature in self.empreintes:
                continue
            self.empreintes.add(signature)
            nouvelles += 1
        self.rejouees = len(essais) - nouvelles

        self.sessions.append(
            {
                "quand": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "hypotheses": nouvelles,
                "rejouees": self.rejouees,
                "detail": detail,
            }
        )
        if self.chemin is None:
            return nouvelles
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self.chemin.write_text(
            json.dumps(
                {
                    "total": self.total,
                    "herite": self.herite,
                    "empreintes": sorted(self.empreintes),
                    "sessions": self.sessions[-200:],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return nouvelles


def seuil_bonferroni(hypotheses: int, alpha: float = ALPHA) -> float:
    """Seuil de t exigé pour rester significatif après `hypotheses` essais.

    Le risque toléré est divisé par le nombre d'essais : chercher plus oblige à
    exiger plus. Une seule hypothèse demande t > 1,96 ; cent en demandent 3,48.
    """
    hypotheses = max(1, hypotheses)
    return NormalDist().inv_cdf(1.0 - alpha / (2.0 * hypotheses))


def evaluer(
    candidates: Sequence[tuple[str, dict, str]],
    candles: Sequence[Candle],
    base: BotConfig,
    split: float = 0.6,
    prechauffe: int = 2000,
    progression=None,
) -> list[Essai]:
    """Exécute chaque candidate sur apprentissage puis validation."""
    coupure = int(len(candles) * split)
    apprentissage = candles[:coupure]
    depart = max(0, coupure - prechauffe)
    validation = candles[depart:]
    decalage = coupure - depart

    essais: list[Essai] = []
    for numero, (strategie, params, label) in enumerate(candidates, 1):
        cfg = _copie(base)
        cfg.strategy = strategie
        cfg.strategy_params = dict(params)
        # `tp_r` est commun à toutes les stratégies : il doit atterrir dans la
        # configuration de risque, sans quoi celles qui ne lisent pas
        # `strategy_params` l'ignorent en silence et la grille ne teste rien.
        if "tp_r" in params:
            cfg.risk.tp_r = float(params["tp_r"])
        # Même piège : la sortie sur le temps est portée par le courtier, pas
        # par la stratégie. Laissée dans `strategy_params`, elle serait ignorée
        # en silence et la grille comparerait des réglages identiques.
        if "max_bars_in_trade" in params:
            cfg.risk.max_bars_in_trade = int(params["max_bars_in_trade"])

        dedans = run_backtest(apprentissage, cfg)
        dehors = run_backtest(validation, cfg, warmup=decalage)
        essai = Essai(
            strategy=strategie,
            label=label,
            params=dict(params),
            dedans=dedans.report,
            dehors=dehors.report,
            refus=dict(dedans.skipped),
        )
        essais.append(essai)
        if progression is not None:
            progression(numero, len(candidates), essai)
    return essais


def _copie(cfg: BotConfig) -> BotConfig:
    """Copie profonde d'une configuration, sans dépendance externe."""
    from copy import deepcopy

    return deepcopy(cfg)


def empreinte(essai: "Essai") -> str:
    """Signature d'une hypothèse, fondée sur son résultat.

    Deux exécutions qui produisent exactement les mêmes trades et la même
    espérance sont la même hypothèse — que ce soit la même commande relancée,
    ou deux réglages dont la différence n'a aucun effet. La correction de
    Bonferroni porte sur le nombre d'hypothèses **distinctes** examinées, pas
    sur le nombre de fois où on a appuyé sur Entrée.

    Les paramètres sont volontairement exclus de la signature : c'est le
    résultat qui fait l'identité. Ajouter un paramètre neutre — une sortie sur
    le temps désactivée, un filtre sans effet — produirait sinon une empreinte
    différente pour un essai rigoureusement identique, et relèverait le seuil
    sans qu'aucune possibilité nouvelle ait été explorée. C'est la même clé que
    `doublons()` applique à l'intérieur d'une session.
    """
    return "|".join(
        (
            essai.strategy,
            str(essai.dedans.trades),
            f"{essai.dedans.expectancy_r:.9f}",
            str(essai.dehors.trades),
            f"{essai.dehors.expectancy_r:.9f}",
        )
    )


def doublons(essais: Sequence[Essai]) -> list[list[Essai]]:
    """Regroupe les candidates dont les résultats sont rigoureusement identiques.

    Deux réglages qui produisent le même résultat au trade près ne sont pas deux
    hypothèses : c'est la même, testée deux fois. Le paramètre censé les
    distinguer n'a aucun effet — et le compteur de Bonferroni s'en trouve
    faussé à la hausse.
    """
    groupes: dict[tuple, list[Essai]] = {}
    for essai in essais:
        cle = (
            essai.strategy,
            essai.dedans.trades,
            round(essai.dedans.expectancy_r, 9),
            essai.dehors.trades,
            round(essai.dehors.expectancy_r, 9),
        )
        groupes.setdefault(cle, []).append(essai)
    return [g for g in groupes.values() if len(g) > 1]


def trades_necessaires(essai: "Essai", seuil: float) -> int | None:
    """Combien de trades il faudrait pour trancher, à effet constant.

    La statistique t croît comme la racine du nombre de trades : quadrupler
    l'échantillon double le t. Si l'avantage observé est réel et se maintient,
    ce chiffre dit exactement quelle quantité de données manque — plutôt que de
    laisser « échantillon insuffisant » sans suite.

    C'est une projection, pas une promesse : rien ne garantit que l'espérance
    tienne sur des données qu'on n'a pas encore vues.
    """
    n = essai.dehors.trades
    t = essai.t_validation
    if n <= 0 or t <= 0:
        return None
    effet = t / (n ** 0.5)          # espérance rapportée à sa dispersion
    if effet <= 0:
        return None
    return int((seuil / effet) ** 2) + 1


@dataclass
class Verdict:
    hypotheses_session: int
    hypotheses_total: int
    seuil_t: float
    meilleur: Essai | None
    significatif: bool
    exploitables: int
    identiques: list[list[Essai]] = field(default_factory=list)
    hypotheses_rejouees: int = 0
    """Candidates de cette session déjà testées à l'identique auparavant."""

    plus_proche: Essai | None = None
    """Meilleure candidate malgré un échantillon insuffisant — pour dire ce qui
    manque, jamais pour conclure."""

    def to_text(self) -> str:
        lignes = [
            "── Verdict statistique ────────────────────────────",
            f"Hypothèses testées cette session : {self.hypotheses_session}",
            f"Hypothèses testées au total      : {self.hypotheses_total}",
            f"Seuil de t exigé (Bonferroni)    : {self.seuil_t:.2f}",
        ]

        if self.hypotheses_rejouees:
            lignes.append(
                f"\n{self.hypotheses_rejouees} candidate(s) déjà testée(s) à "
                "l'identique : non recomptée(s).\n"
                "  Relancer la même commande n'examine pas de nouvelle "
                "hypothèse, donc ne relève pas le seuil."
            )

        for groupe in self.identiques:
            noms = ", ".join(e.label for e in groupe)
            lignes.append(
                f"\n⚠ Résultats identiques : {noms}\n"
                "  Le paramètre qui les distingue n'a aucun effet sur cette "
                "stratégie. Ce n'est qu'une seule hypothèse, pas plusieurs."
            )

        if self.meilleur is None:
            lignes.append(
                f"\nAucune candidate n'atteint {MIN_TRADES} trades de chaque côté. "
                "Rien à conclure."
            )
            if self.plus_proche is not None:
                p = self.plus_proche
                besoin = trades_necessaires(p, self.seuil_t)
                lignes.append(
                    f"\nLa plus fournie est {p.label} : "
                    f"{p.dedans.trades} / {p.dehors.trades} trades, "
                    f"{p.dedans.expectancy_r:+.3f} R → {p.dehors.expectancy_r:+.3f} R"
                    + (
                        f"\nIl en faudrait environ {besoin} en validation pour "
                        f"pouvoir trancher, soit {besoin / max(1, p.dehors.trades):.0f} "
                        "fois plus de données."
                        if besoin
                        else ""
                    )
                )
            return "\n".join(lignes)

        m = self.meilleur
        lignes.append(
            f"\nMeilleure : {m.label}\n"
            f"  apprentissage {m.dedans.expectancy_r:+.3f} R "
            f"({m.dedans.trades} trades)\n"
            f"  validation    {m.dehors.expectancy_r:+.3f} R "
            f"({m.dehors.trades} trades), t = {m.t_validation:.2f}"
        )

        if m.dehors.expectancy_r <= 0:
            lignes.append(
                "\nLa meilleure candidate perd hors échantillon. Rien à retenir."
            )
        elif self.significatif:
            lignes.append(
                f"\nt = {m.t_validation:.2f} > {self.seuil_t:.2f} : le résultat "
                f"survit à la correction pour {self.hypotheses_total} hypothèses.\n"
                "C'est le meilleur signal qu'un backtest puisse donner — et ça "
                "ne remplace toujours pas plusieurs mois de démo en temps réel."
            )
        else:
            manque = self.seuil_t - m.t_validation
            texte = (
                f"\nt = {m.t_validation:.2f} < {self.seuil_t:.2f} : il manque "
                f"{manque:.2f} pour être distinguable du hasard,\n"
                f"compte tenu des {self.hypotheses_total} hypothèses déjà testées. "
                "Le résultat est peut-être réel, mais rien ici ne permet\n"
                "de l'affirmer."
            )
            besoin = trades_necessaires(m, self.seuil_t)
            if besoin:
                facteur = besoin / max(1, m.dehors.trades)
                texte += (
                    f"\n\nÀ effet constant, il faudrait environ {besoin} trades "
                    f"en validation contre {m.dehors.trades} aujourd'hui,\n"
                    f"soit à peu près {facteur:.0f} fois plus de données. "
                    "Le t croît comme la racine du nombre de trades.\n"
                    "Allonger l'historique est donc la seule voie utile : "
                    "multiplier les réglages ne ferait que relever la barre."
                )
            lignes.append(texte)
        return "\n".join(lignes)


def juger(essais: Sequence[Essai], journal: Journal, detail: str = "") -> Verdict:
    """Classe les candidates et tranche, correction du multi-test incluse."""
    nouvelles = journal.enregistrer(essais, detail)
    exploitables = [e for e in essais if e.exploitable]
    seuil = seuil_bonferroni(journal.total)

    meilleur = (
        max(exploitables, key=lambda e: e.dedans.expectancy_r)
        if exploitables
        else None
    )
    significatif = bool(
        meilleur
        and meilleur.dehors.expectancy_r > 0
        and meilleur.t_validation > seuil
    )
    prometteuse = None
    if not exploitables and essais:
        positives = [e for e in essais if e.dehors.expectancy_r > 0]
        if positives:
            prometteuse = max(positives, key=lambda e: e.dehors.trades)

    return Verdict(
        plus_proche=prometteuse,
        identiques=doublons(essais),
        hypotheses_session=nouvelles,
        hypotheses_total=journal.total,
        hypotheses_rejouees=journal.rejouees,
        seuil_t=seuil,
        meilleur=meilleur,
        significatif=significatif,
        exploitables=len(exploitables),
    )
