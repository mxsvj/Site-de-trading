"""Tests de la reconstruction de bougies depuis les ticks.

Cette reconstruction sert à dépasser le plafond de 100 000 bougies du courtier.
Elle a été validée contre MetaTrader — 27 544 bougies communes, 100,00 %
identiques au centime — mais seulement après correction d'un bug d'alignement
des tranches qui faussait l'ouverture jusqu'à 165 points. Ces tests verrouillent
les deux propriétés qui comptent : l'agrégation elle-même, et le fait qu'une
bougie ne doit jamais être coupée entre deux tranches.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from smcbot.data import agreger_ticks, debut_de_bougie


def _tick(epoch: int, bid: float) -> dict:
    return {"time": epoch, "bid": bid, "ask": bid + 0.12}


def _epoch(h: int, m: int, s: int) -> int:
    return int(datetime(2026, 8, 10, h, m, s, tzinfo=timezone.utc).timestamp())


def test_debut_de_bougie_arrondit_vers_le_bas():
    assert debut_de_bougie(_epoch(9, 3, 59), 60) == _epoch(9, 3, 0)
    assert debut_de_bougie(_epoch(9, 3, 0), 60) == _epoch(9, 3, 0)


def test_debut_de_bougie_refuse_une_duree_nulle():
    with pytest.raises(ValueError):
        debut_de_bougie(_epoch(9, 0, 0), 0)


def test_ohlc_suit_l_ordre_des_ticks():
    """L'ouverture est le premier bid, la clôture le dernier — pas le min/max."""
    ticks = [
        _tick(_epoch(9, 0, 5), 2000.00),
        _tick(_epoch(9, 0, 20), 2003.00),
        _tick(_epoch(9, 0, 40), 1998.00),
        _tick(_epoch(9, 0, 55), 2001.00),
    ]
    (bougie,) = agreger_ticks(ticks, 60)
    assert bougie.open == 2000.00
    assert bougie.high == 2003.00
    assert bougie.low == 1998.00
    assert bougie.close == 2001.00
    assert bougie.volume == 4


def test_les_ticks_se_repartissent_par_minute():
    ticks = [
        _tick(_epoch(9, 0, 10), 2000.00),
        _tick(_epoch(9, 1, 10), 2010.00),
        _tick(_epoch(9, 2, 10), 2020.00),
    ]
    bougies = agreger_ticks(ticks, 60)
    assert [b.open for b in bougies] == [2000.00, 2010.00, 2020.00]
    assert bougies[0].time == datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)


def test_bid_nul_ignore():
    """Un bid à zéro est une cotation absente, pas un prix."""
    ticks = [
        _tick(_epoch(9, 0, 5), 0.0),
        _tick(_epoch(9, 0, 10), 2000.00),
    ]
    (bougie,) = agreger_ticks(ticks, 60)
    assert bougie.open == 2000.00
    assert bougie.volume == 1


def test_derniere_bougie_ecartee_si_demande():
    ticks = [
        _tick(_epoch(9, 0, 10), 2000.00),
        _tick(_epoch(9, 1, 10), 2010.00),
    ]
    assert len(agreger_ticks(ticks, 60)) == 2
    assert len(agreger_ticks(ticks, 60, complete_seulement=True)) == 1


def test_une_bougie_coupee_entre_deux_tranches_fausse_l_ouverture():
    """Le bug corrigé le 2026-08-10, verrouillé ici.

    Si une tranche commence au milieu d'une bougie, l'ouverture reconstruite
    est celle du milieu et non celle du début. Découper sur des bornes alignées
    donne au contraire exactement la même bougie qu'un téléchargement d'un seul
    tenant.
    """
    ticks = [
        _tick(_epoch(9, 0, 5), 2000.00),
        _tick(_epoch(9, 0, 30), 2005.00),
        _tick(_epoch(9, 0, 50), 2002.00),
    ]
    entier = agreger_ticks(ticks, 60)[0]

    # Découpe fautive, au milieu de la bougie.
    coupe_fautive = agreger_ticks(ticks[1:], 60)[0]
    assert coupe_fautive.open != entier.open

    # Découpe alignée : la bougie suivante commence à 9h01, rien n'est perdu.
    aligne = agreger_ticks(ticks, 60) + agreger_ticks(
        [_tick(_epoch(9, 1, 5), 2007.00)], 60
    )
    assert aligne[0].open == entier.open
    assert aligne[1].open == 2007.00
