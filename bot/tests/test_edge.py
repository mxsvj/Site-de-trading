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
