"""Tests du modèle ATM : range, prise de liquidité, IFVG.

Le test qui compte n'est pas qu'un signal sorte — c'est que les trois règles
qui le conditionnent soient réellement appliquées : le plafond de dépassement,
le stop au-delà de l'extrême balayé, et le breakeven à mi-range.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from smcbot.atm import AtmStrategy
from smcbot.config import BotConfig, xauusd
from smcbot.data import Candle
from smcbot.smc import BEARISH, BULLISH

JOUR = datetime(2026, 6, 2)          # un mardi


def _cfg(**params) -> BotConfig:
    cfg = BotConfig(symbol=xauusd(), strategy="atm")
    cfg.strategy_params = dict(params)
    return cfg


def _a(h: int, m: int) -> datetime:
    return JOUR.replace(hour=h, minute=m)


def _range(haut: float = 2010.0, bas: float = 2000.0) -> list[Candle]:
    """Bougies de 13:00 à 15:24 balayant tout le range.

    Ouverture et clôture au milieu, amplitude proportionnelle : le gabarit
    reste cohérent quelles que soient les bornes demandées.
    """
    bougies = []
    debut = _a(13, 0)
    milieu = (haut + bas) / 2.0
    ecart = (haut - bas) / 5.0
    for i in range(145):                       # 13:00 → 15:24
        moment = debut + timedelta(minutes=i)
        haut_i = haut if i % 2 == 0 else haut - ecart
        bas_i = bas if i % 2 == 1 else bas + ecart
        bougies.append(Candle(moment, milieu, haut_i, bas_i, milieu, 1.0))
    return bougies


def _sequence_short(depassement: float) -> list[Candle]:
    """Poussée laissant un FVG haussier, balayage du haut, puis inversion."""
    sommet = 2010.0 + depassement
    return [
        # P1 — départ de la poussée
        Candle(_a(15, 25), 2008.0, 2008.5, 2007.5, 2008.4, 1.0),
        # P2 — impulsion
        Candle(_a(15, 26), 2008.4, 2009.6, 2008.4, 2009.5, 1.0),
        # P3 — balaie le haut ET laisse un FVG haussier (low > P1.high)
        Candle(_a(15, 27), 2009.5, sommet, 2008.7, sommet - 0.10, 1.0),
        # P4 — clôture sous le FVG : inversion
        Candle(_a(15, 28), sommet - 0.10, sommet - 0.05, 2008.4, 2008.45, 1.0),
    ]


def _jouer(strat: AtmStrategy, bougies):
    signaux = []
    for c in bougies:
        s = strat.on_candle(c)
        if s is not None:
            signaux.append(s)
    return signaux


def test_balayage_puis_ifvg_donne_un_signal_contraire():
    strat = AtmStrategy(_cfg())
    signaux = _jouer(strat, _range() + _sequence_short(0.20))

    assert len(signaux) == 1
    s = signaux[0]
    assert s.direction == BEARISH, "un balayage du haut doit donner une vente"
    assert s.entry_type == "market"
    # Stop au-delà de l'extrême balayé (2010.20) plus la marge de 5 points.
    assert s.stop == pytest.approx(2010.25)
    assert s.stop > s.entry_level


def test_le_stop_se_place_au_dela_de_l_extreme_balaye():
    """Pas au-delà du bord du range : c'est la mèche du balayage qui invalide."""
    strat = AtmStrategy(_cfg(stop_buffer_points=0.0))
    signaux = _jouer(strat, _range() + _sequence_short(0.30))
    assert signaux[0].stop == pytest.approx(2010.30)


def test_un_depassement_trop_ample_disqualifie_la_journee():
    """Au-delà du seuil, ce n'est plus une prise de liquidité mais une cassure.

    Prendre le contrepied d'une vraie sortie de range est le principal mode
    d'échec de ce modèle : le filtre doit donc écarter la journée entière, pas
    seulement la bougie fautive.
    """
    strat = AtmStrategy(_cfg(max_sweep_points=40.0))
    signaux = _jouer(strat, _range() + _sequence_short(0.60))   # 60 points
    assert signaux == []
    assert strat.jour_disqualifie


def test_le_seuil_de_depassement_est_bien_le_parametre():
    """Le même scénario passe ou non selon le seuil, donc il agit vraiment."""
    passe = _jouer(AtmStrategy(_cfg(max_sweep_points=60.0)), _range() + _sequence_short(0.50))
    refuse = _jouer(AtmStrategy(_cfg(max_sweep_points=40.0)), _range() + _sequence_short(0.50))
    assert len(passe) == 1
    assert refuse == []


def test_breakeven_place_a_la_moitie_du_range():
    strat = AtmStrategy(_cfg())
    signaux = _jouer(strat, _range(haut=2010.0, bas=2000.0) + _sequence_short(0.20))
    assert signaux[0].breakeven_price == pytest.approx(2005.0)


def test_pas_de_breakeven_si_le_milieu_est_deja_franchi():
    """Un niveau derrière l'entrée déclencherait le breakeven immédiatement.

    Le stop passerait à l'entrée dès la première bougie, ce qui transformerait
    le modèle en autre chose sans que rien ne le signale.
    """
    # Range étroit : milieu à 2008.50, au-dessus de l'entrée de la vente
    # (2008.45). Son bas reste sous la séquence, sinon c'est le bas qui serait
    # balayé et le scénario ne dirait plus rien du breakeven.
    strat = AtmStrategy(_cfg())
    signaux = _jouer(strat, _range(haut=2010.0, bas=2007.0) + _sequence_short(0.20))
    assert signaux, "le scénario doit produire un signal"
    assert strat.milieu > signaux[0].entry_level, "sinon le test ne teste rien"
    assert signaux[0].breakeven_price is None


def test_un_seul_trade_par_jour():
    strat = AtmStrategy(_cfg())
    suite = _sequence_short(0.20) + [
        Candle(_a(15, 40), 2008.0, 2008.2, 2007.0, 2007.1, 1.0),
        Candle(_a(15, 41), 2007.1, 2007.3, 2006.0, 2006.1, 1.0),
    ]
    assert len(_jouer(strat, _range() + suite)) == 1


def test_rien_sans_balayage():
    """Un range qui tient ne produit aucun signal."""
    strat = AtmStrategy(_cfg())
    calme = [
        Candle(_a(15, 25 + i), 2005.0, 2006.0, 2004.0, 2005.5, 1.0)
        for i in range(20)
    ]
    assert _jouer(strat, _range() + calme) == []


def test_le_range_se_reinitialise_chaque_jour():
    strat = AtmStrategy(_cfg())
    _jouer(strat, _range() + _sequence_short(0.20))
    veille = strat.haut

    lendemain = [
        Candle(c.time + timedelta(days=1), c.open, c.high, c.low, c.close, c.volume)
        for c in _range(haut=2050.0, bas=2040.0)
    ]
    _jouer(strat, lendemain)
    assert strat.haut == pytest.approx(2050.0)
    assert strat.haut != veille
    assert not strat.pris, "le compteur de trade doit repartir à zéro"


def test_balayage_du_bas_donne_un_achat():
    """Symétrie : le modèle doit fonctionner dans les deux sens."""
    strat = AtmStrategy(_cfg())
    creux = 2000.0 - 0.20
    suite = [
        Candle(_a(15, 25), 2002.0, 2002.5, 2001.5, 2001.6, 1.0),
        Candle(_a(15, 26), 2001.6, 2001.6, 2000.4, 2000.5, 1.0),
        # Balaie le bas et laisse un FVG baissier (high < P1.low)
        Candle(_a(15, 27), 2000.5, 2001.3, creux, creux + 0.10, 1.0),
        # Clôture au-dessus du FVG : inversion
        Candle(_a(15, 28), creux + 0.10, 2001.6, creux + 0.05, 2001.55, 1.0),
    ]
    signaux = _jouer(strat, _range() + suite)
    assert len(signaux) == 1
    assert signaux[0].direction == BULLISH
    assert signaux[0].stop < signaux[0].entry_level
