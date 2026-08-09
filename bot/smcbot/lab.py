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

    @property
    def exploitable(self) -> bool:
        return (
            self.dedans.trades >= MIN_TRADES and self.dehors.trades >= MIN_TRADES
        )

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
        self.total = 0
        self.sessions: list[dict] = []
        if self.chemin and self.chemin.exists():
            self._charger()

    def _charger(self) -> None:
        assert self.chemin is not None
        try:
            brut = json.loads(self.chemin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.total = int(brut.get("total", 0))
        self.sessions = list(brut.get("sessions", []))

    def enregistrer(self, nombre: int, detail: str) -> None:
        self.total += nombre
        self.sessions.append(
            {
                "quand": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "hypotheses": nombre,
                "detail": detail,
            }
        )
        if self.chemin is None:
            return
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self.chemin.write_text(
            json.dumps(
                {"total": self.total, "sessions": self.sessions[-200:]},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


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

        essai = Essai(
            strategy=strategie,
            label=label,
            params=dict(params),
            dedans=run_backtest(apprentissage, cfg).report,
            dehors=run_backtest(validation, cfg, warmup=decalage).report,
        )
        essais.append(essai)
        if progression is not None:
            progression(numero, len(candidates), essai)
    return essais


def _copie(cfg: BotConfig) -> BotConfig:
    """Copie profonde d'une configuration, sans dépendance externe."""
    from copy import deepcopy

    return deepcopy(cfg)


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


@dataclass
class Verdict:
    hypotheses_session: int
    hypotheses_total: int
    seuil_t: float
    meilleur: Essai | None
    significatif: bool
    exploitables: int
    identiques: list[list[Essai]] = field(default_factory=list)

    def to_text(self) -> str:
        lignes = [
            "── Verdict statistique ────────────────────────────",
            f"Hypothèses testées cette session : {self.hypotheses_session}",
            f"Hypothèses testées au total      : {self.hypotheses_total}",
            f"Seuil de t exigé (Bonferroni)    : {self.seuil_t:.2f}",
        ]

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
            lignes.append(
                f"\nt = {m.t_validation:.2f} < {self.seuil_t:.2f} : il manque "
                f"{manque:.2f} pour être distinguable du hasard,\n"
                f"compte tenu des {self.hypotheses_total} hypothèses déjà testées. "
                "Le résultat est peut-être réel, mais rien ici ne permet\n"
                "de l'affirmer. Le tester davantage ne le rendra pas plus "
                "significatif : ça relèvera encore la barre."
            )
        return "\n".join(lignes)


def juger(essais: Sequence[Essai], journal: Journal, detail: str = "") -> Verdict:
    """Classe les candidates et tranche, correction du multi-test incluse."""
    journal.enregistrer(len(essais), detail)
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
    return Verdict(
        identiques=doublons(essais),
        hypotheses_session=len(essais),
        hypotheses_total=journal.total,
        seuil_t=seuil,
        meilleur=meilleur,
        significatif=significatif,
        exploitables=len(exploitables),
    )
