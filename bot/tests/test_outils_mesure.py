"""Contrôles positifs des outils de mesure sur ticks.

Ces outils ont produit plusieurs verdicts négatifs — 30 s, 60 s, microstructure,
avance de l'argent. Or un verdict négatif issu d'un code cassé est indiscernable
d'un verdict négatif correct : si la réduction des ticks en observations détruit
le signal, « rien trouvé » ne veut plus rien dire.

`test_edge.py` pose déjà le principe pour le balayage sur bougies : le test qui
compte n'est pas qu'on ne trouve rien dans du bruit, c'est qu'on **retrouve un
avantage délibérément caché**. Ces tests appliquent la même exigence à la
chaîne ticks → observations, et vérifient en plus les deux propriétés dont
dépendent tous les t affichés : fenêtres réellement disjointes, et conditions
strictement antérieures à la décision.

Les modules vivent dans `outils_mesure/`, qui n'est pas un paquet : on les
charge par chemin. Leur import de MetaTrader5 est local aux fonctions de
collecte, donc ils s'importent sans terminal.
"""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

OUTILS = Path(__file__).resolve().parent.parent / "outils_mesure"


def _charger(nom: str):
    chemin = OUTILS / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


micro = _charger("microstructure_tick")
lead = _charger("lead_lag_argent")

FLAG_BID, FLAG_ASK, FLAG_LES_DEUX = 2, 4, 6


def _tick(t: int, bid: float, flags: int = FLAG_LES_DEUX, spread: float = 0.12):
    return {"time": t, "bid": bid, "ask": bid + spread, "flags": flags}


def _ticks_avec_signal(
    secondes: int = 6000, regime: int = 300, effet: float = 0.50, graine: int = 7
):
    """Ticks où le côté qui se repositionne annonce vraiment la suite.

    Par blocs de `regime` secondes, le flux est soit dominé par des mises à jour
    du bid seul et le prix monte, soit l'inverse. Un outil correct doit retrouver
    cette association ; un outil cassé ne le peut pas.
    """
    alea = random.Random(graine)
    ticks = []
    prix = 2000.0
    for s in range(secondes):
        monte = (s // regime) % 2 == 0
        prix += (effet if monte else -effet) / regime
        prix += alea.uniform(-0.02, 0.02)
        for _ in range(3):  # ~3 ticks par seconde, comme sur l'or
            if alea.random() < 0.25:
                drapeau = FLAG_BID if monte else FLAG_ASK
            else:
                drapeau = FLAG_LES_DEUX
            ticks.append(_tick(s, prix, drapeau))
    return ticks


# ── Propriétés dont dépendent tous les t affichés ────────────────────────


def test_fenetres_reellement_disjointes():
    """Deux fenêtres qui se recouvrent gonflent le t d'environ racine(horizon)."""
    obs = micro._tranche(_ticks_avec_signal(3000), 0.01, 30, 60, 0)
    assert len(obs) > 20
    instants = [o["t"] for o in obs]
    ecarts = [b - a for a, b in zip(instants, instants[1:])]
    assert min(ecarts) >= 30, f"fenêtres qui se recouvrent : écart min {min(ecarts)}"


def test_aucune_condition_ne_regarde_le_futur():
    """Modifier l'après ne doit changer aucune condition, seulement le rendement.

    C'est le lookahead qui a coûté +0,9 R par trade au backtest. Ici il
    fabriquerait un avantage spectaculaire et faux.
    """
    base = _ticks_avec_signal(3000)
    obs_a = micro._tranche(base, 0.01, 30, 60, 0)

    # On écrase brutalement tout ce qui suit la moitié de la série.
    milieu = len(base) // 2
    trafique = base[:milieu] + [
        _tick(t["time"], t["bid"] + 50.0, t["flags"]) for t in base[milieu:]
    ]
    obs_b = micro._tranche(trafique, 0.01, 30, 60, 0)

    par_t = {o["t"]: o for o in obs_b}
    compares = 0
    for o in obs_a:
        jumeau = par_t.get(o["t"])
        if jumeau is None:
            continue
        # Une observation dont la fenêtre entière précède la trafique doit être
        # identique en tout point, rendement compris.
        if o["t"] + 30 < base[milieu]["time"] - 60:
            assert o["desequilibre"] == pytest.approx(jumeau["desequilibre"])
            assert o["cadence"] == pytest.approx(jumeau["cadence"])
            assert o["r"] == pytest.approx(jumeau["r"])
            compares += 1
    assert compares > 10, "trop peu d'observations comparables"


# ── Contrôle positif : retrouve-t-on un avantage caché ? ─────────────────


def test_retrouve_un_avantage_delibrement_cache():
    """Sans ce test, tous les verdicts négatifs de ces outils seraient vides."""
    obs = micro._tranche(_ticks_avec_signal(12000), 0.01, 30, 60, 0)
    assert len(obs) > 100

    import statistics

    globale = statistics.fmean(o["r"] for o in obs)
    lignes = micro.juger(micro.cellules(obs), globale)
    cote = [c for c in lignes if c["critere"] == "côté qui bouge"]
    assert cote, "le critère du côté qui bouge a disparu"

    plus_fort = max(cote, key=lambda c: abs(c["t"]))
    assert abs(plus_fort["t"]) > 4.0, (
        "l'avantage caché n'est pas retrouvé : la réduction des ticks en "
        f"observations le détruit (t max {plus_fort['t']:.2f})"
    )


def test_bruit_pur_ne_produit_rien():
    """Le pendant du contrôle positif : ne pas inventer d'avantage."""
    alea = random.Random(11)
    ticks, prix = [], 2000.0
    for s in range(12000):
        prix += alea.uniform(-0.05, 0.05)
        for _ in range(3):
            drapeau = alea.choice([FLAG_BID, FLAG_ASK, FLAG_LES_DEUX])
            ticks.append(_tick(s, prix, drapeau))

    import statistics

    obs = micro._tranche(ticks, 0.01, 30, 60, 0)
    globale = statistics.fmean(o["r"] for o in obs)
    lignes = micro.juger(micro.cellules(obs), globale)
    assert lignes
    assert max(abs(c["t"]) for c in lignes) < 4.0, (
        "un avantage apparaît là où il n'y a que du bruit"
    )


# ── L'avance d'un instrument sur l'autre est-elle bien captée ? ──────────


def test_lead_lag_retrouve_une_avance_construite():
    """L'argent bouge, l'or suit 30 s plus tard : l'outil doit le voir."""
    alea = random.Random(5)
    b_or: dict[int, float] = {}
    s_or: dict[int, float] = {}
    b_ag: dict[int, float] = {}
    or_, ag = 2000.0, 25.0
    pas_ag = [alea.uniform(-0.01, 0.01) for _ in range(9000)]

    for s in range(9000):
        ag += pas_ag[s]
        # L'or reproduit le mouvement de l'argent avec 30 s de retard.
        or_ += (pas_ag[s - 30] * 40.0) if s >= 30 else 0.0
        or_ += alea.uniform(-0.02, 0.02)
        b_or[s], b_ag[s] = or_, ag
        s_or[s] = 0.12

    obs = lead._tranche(b_or, s_or, b_ag, 0.01, 0.001, 30, 30, 0)
    assert len(obs) > 100

    import statistics

    globale = statistics.fmean(o["r"] for o in obs)
    lignes = lead.juger(lead.cellules(obs), globale)
    avance = [c for c in lignes if c["critere"] == "avance de l'argent"]
    assert avance, "le critère d'avance de l'argent a disparu"
    assert max(abs(c["t"]) for c in avance) > 4.0, (
        "une avance construite de toutes pièces n'est pas retrouvée"
    )
