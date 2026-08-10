"""Tests du balayage de conditions prédictives.

Le test décisif de ce module n'est pas qu'il ne trouve rien sur du bruit —
n'importe quel code cassé y parvient. C'est qu'il **retrouve un avantage
qu'on y a délibérément caché**. Sans ce contrôle positif, un résultat nul ne
prouverait rien.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from smcbot.data import Candle
from smcbot.edge import MIN_ECHANTILLON, balayer


def _serie(n: int, graine: int = 3, biais_heure: int | None = None,
           force: float = 0.0) -> list[Candle]:
    """Marche aléatoire, avec au besoin une dérive cachée à une heure donnée."""
    rng = random.Random(graine)
    prix = 2650.0
    debut = datetime(2025, 3, 3, 0, 0)   # un lundi
    bougies = []
    for i in range(n):
        moment = debut + timedelta(minutes=i)
        derive = force if (biais_heure is not None
                           and moment.hour == biais_heure) else 0.0
        pas = rng.gauss(derive, 1.0)
        ouverture = prix
        prix = max(1.0, prix + pas)
        haut = max(ouverture, prix) + abs(rng.gauss(0, 0.3))
        bas = min(ouverture, prix) - abs(rng.gauss(0, 0.3))
        bougies.append(Candle(moment, ouverture, haut, bas, prix, 1.0))
    return bougies


def test_ne_trouve_rien_dans_une_marche_aleatoire():
    balayage = balayer(_serie(40000), horizon=15)
    assert balayage.examinees, "aucune cellule exploitable : test sans valeur"
    survivants = [c for c in balayage.examinees if abs(c.t) > balayage.seuil]
    assert not survivants, (
        f"détecte un avantage dans du bruit pur : {survivants[:3]}"
    )


def test_retrouve_un_avantage_delibérement_cache():
    """Contrôle positif : sans lui, un résultat nul ne prouverait rien."""
    balayage = balayer(_serie(40000, biais_heure=10, force=0.35), horizon=15)

    survivants = [c for c in balayage.examinees if abs(c.t) > balayage.seuil]
    assert survivants, "n'a pas retrouvé une dérive pourtant injectée"

    heures = [c for c in survivants if c.critere == "heure"]
    assert heures, f"avantage détecté ailleurs qu'à l'heure attendue : {survivants}"
    assert heures[0].valeur == "10h"
    assert heures[0].moyenne > 0


def test_echantillons_disjoints():
    """Des fenêtres qui se recouvrent gonfleraient le t d'environ racine(horizon).

    Une bougie sur `horizon` seulement doit être retenue : sinon le rendement
    de la bougie i et celui de la bougie i+1 partagent presque toute leur
    trajectoire, et les traiter comme indépendants fabrique de la
    significativité à partir de rien.
    """
    n, horizon = 6000, 15
    balayage = balayer(_serie(n), horizon=horizon)
    attendu = (n - horizon - 15) // horizon
    assert abs(balayage.observations - attendu) <= 2


def test_seuil_monte_avec_le_nombre_de_cellules():
    petit = balayer(_serie(40000), horizon=15)
    assert petit.seuil > 1.96, "aucune correction du multi-test appliquée"
    # Soixante cellules exigent nettement plus qu'une seule.
    assert petit.seuil > 3.0


def test_cellules_trop_petites_sont_ecartees():
    balayage = balayer(_serie(40000), horizon=15)
    for cellule in balayage.examinees:
        assert cellule.n >= MIN_ECHANTILLON


def test_historique_trop_court_est_refuse():
    with pytest.raises(ValueError):
        balayer(_serie(50), horizon=15)


def test_le_rendement_est_normalise_par_la_volatilite():
    """Sinon les heures calmes et agitées seraient incomparables."""
    balayage = balayer(_serie(40000), horizon=15)
    # Exprimés en multiples d'ATR, les rendements restent d'ordre 1.
    for cellule in balayage.examinees:
        assert abs(cellule.moyenne) < 20.0


def test_confrontation_conserve_un_effet_reel():
    """Un avantage injecté doit survivre au passage hors échantillon."""
    from smcbot.edge import confronter

    couples, seuil = confronter(
        _serie(60000, biais_heure=10, force=0.35), horizon=15, split=0.6
    )
    dix = [c for c in couples if c.critere == "heure" and c.valeur == "10h"]
    assert dix, "la cellule injectée n'est pas comparable des deux côtés"
    assert dix[0].meme_sens, "l'effet injecté change de signe hors échantillon"
    assert dix[0].dedans.moyenne > 0 and dix[0].dehors.moyenne > 0


def test_confrontation_denonce_le_bruit():
    """Sur du bruit, les meilleures cellules ne doivent pas tenir des deux côtés."""
    from smcbot.edge import confronter

    couples, seuil = confronter(_serie(60000), horizon=15, split=0.6)
    assert couples, "aucune cellule comparable : test sans valeur"
    survivants = [
        c for c in couples
        if abs(c.dedans.t) > seuil and abs(c.dehors.t) > seuil and c.meme_sens
    ]
    assert not survivants, f"du bruit tient des deux côtés : {survivants[:2]}"

    # Et une bonne part des meilleures cellules doit changer de signe.
    tetes = couples[:10]
    assert sum(1 for c in tetes if not c.meme_sens) >= 2


def test_effet_rentable_est_en_multiples_d_atr():
    """L'ATR est en prix, le spread en points : les confondre donne un non-sens.

    Le seuil de rentabilité doit être comparable aux moyennes des cellules,
    qui sont en multiples d'ATR. Une erreur d'unité produisait « 26 ATR »
    au lieu de « 0,26 ATR » — et faisait passer n'importe quel balayage pour
    largement assez puissant.
    """
    balayage = balayer(_serie(20000), horizon=15)
    balayage.atr_median = 2.00          # 200 points si le point vaut 0,01
    assert balayage.effet_rentable(24.0, 0.01) == pytest.approx(0.12)
    # Un spread deux fois plus large exige un effet deux fois plus grand.
    assert balayage.effet_rentable(48.0, 0.01) == pytest.approx(0.24)


def test_puissance_compare_ce_qu_on_voit_a_ce_qu_il_faut():
    """Un résultat nul n'a de sens que si l'on sait ce qu'on aurait pu voir."""
    balayage = balayer(_serie(40000), horizon=15)
    detectable = balayage.effet_detectable()
    assert 0.0 < detectable < 1.0, "finesse de détection invraisemblable"
    # Sur 40 000 bougies, on doit distinguer un effet de quelques centièmes
    # d'ATR sur les cellules les mieux fournies.
    assert detectable < 0.30


def test_le_seuil_de_frais_est_propre_a_chaque_cellule():
    """Une cellule à faible volatilité paie le spread plus cher.

    Lui appliquer le seuil calculé sur l'ATR médian du marché flatterait
    précisément les conditions calmes — celles où le spread pèse le plus.
    """
    from smcbot.edge import Cellule

    calme = Cellule("volatilité", "Q2", 6659, 0.061, 1.0, atr_moyen=3.00)
    agitee = Cellule("volatilité", "Q5", 6659, 0.061, 1.0, atr_moyen=8.00)

    # 24 points de spread, point à 0,01 : 300 points contre 800 points d'ATR.
    assert calme.rentable_a_partir_de(24.0, 0.01) == pytest.approx(0.08)
    assert agitee.rentable_a_partir_de(24.0, 0.01) == pytest.approx(0.03)

    assert not calme.couvre_ses_frais(24.0, 0.01)
    assert agitee.couvre_ses_frais(24.0, 0.01)


def test_l_atr_moyen_est_renseigne_par_cellule():
    balayage = balayer(_serie(40000), horizon=15)
    volatilite = [c for c in balayage.examinees if c.critere == "volatilité (ATR)"]
    assert len(volatilite) >= 4
    par_quintile = {c.valeur: c.atr_moyen for c in volatilite}
    # Les quintiles doivent être ordonnés : Q1 plus calme que Q5.
    assert par_quintile["Q1"] < par_quintile["Q5"]


def test_une_derive_pure_ne_cree_aucune_cellule_significative():
    """Un marché qui monte sans condition ne doit rien faire ressortir.

    Sinon, à long horizon, toutes les cellules héritent de la tendance et l'une
    d'elles finit par « couvrir ses frais » sans rien apprendre. C'est ce qui
    s'était produit le 2026-08-10 sur 696 371 bougies M1 : à 60 bougies
    d'horizon, l'or dérivait de +22,7 points par fenêtre et toutes les cellules
    ressortaient positives.
    """
    debut = datetime(2026, 1, 1)
    bougies = []
    prix = 2000.0
    for i in range(4000):
        # Hausse régulière, plus un bruit sans lien avec la moindre condition.
        prix += 0.05 + random.Random(i).uniform(-0.10, 0.10)
        bougies.append(
            Candle(debut + timedelta(minutes=i), prix, prix + 0.3, prix - 0.3, prix)
        )

    balayage = balayer(bougies, horizon=12)
    assert balayage.moyenne_globale > 0, "la serie doit bien deriver vers le haut"

    seuil = balayage.seuil
    survivants = [c for c in balayage.examinees if abs(c.t) > seuil]
    assert not survivants, (
        "une derive sans condition ne doit produire aucune cellule "
        f"significative, or : {[(c.critere, c.valeur, round(c.t, 2)) for c in survivants]}"
    )


def test_l_exces_retire_la_derive():
    """`exces` mesure l'ecart au marche sans condition, pas le rendement brut."""
    debut = datetime(2026, 1, 1)
    prix = 2000.0
    bougies = []
    for i in range(2000):
        prix += 0.05
        bougies.append(
            Candle(debut + timedelta(minutes=i), prix, prix + 0.3, prix - 0.3, prix)
        )
    balayage = balayer(bougies, horizon=6)
    for cellule in balayage.examinees:
        assert cellule.exces == pytest.approx(
            cellule.moyenne - balayage.moyenne_globale
        )
