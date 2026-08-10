"""Tests du modèle de remplissage des ordres limite.

Le moteur remplissait un ordre limite dès que la bougie touchait son niveau, en
totalité et au meilleur prix de l'excursion. Un plus bas qui effleure le niveau
au centième près ne sert pourtant personne : il faut que le marché traite
au-delà pour purger la file d'attente.

Le biais jouait en faveur des entrées limite — donc précisément dans le sens
qui aurait pu faire croire qu'elles échappent au spread.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from smcbot.broker import PaperBroker
from smcbot.config import BotConfig, SymbolSpec
from smcbot.data import Candle
from smcbot.strategy import Signal


@pytest.fixture
def cfg() -> BotConfig:
    c = BotConfig()
    c.symbol = SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        value_per_point_per_lot=1.0,
        min_lot=0.01,
        lot_step=0.01,
        spread_points=12.0,
    )
    c.risk.initial_balance = 100_000.0
    c.risk.limit_fill_margin_points = 1.0
    c.filters.sessions = []
    c.filters.min_stop_points = 0.0
    c.filters.max_cost_ratio = 0.0
    return c


def _bougie(open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(datetime(2026, 8, 10, 9, 0), open_, high, low, close)


def _signal(level: float, stop: float, direction: str = "bullish") -> Signal:
    return Signal(
        index=1,
        time=datetime(2026, 8, 10, 9, 0),
        direction=direction,
        entry_level=level,
        stop=stop,
        tp_r=2.0,
        reason="test",
        entry_type="limit",
    )


def test_niveau_seulement_effleure_ne_remplit_pas(cfg):
    """Le plus bas touche le niveau au centime près : personne n'est servi."""
    courtier = PaperBroker(cfg)
    bougie = _bougie(2010.00, 2011.00, 2000.00, 2005.00)
    # Niveau exactement au plus bas de la bougie.
    position = courtier.execute(_signal(level=2000.00, stop=1995.00), bougie, 1)
    assert position is None


def test_niveau_traverse_remplit(cfg):
    """Le marché cote au-delà du niveau : l'ordre est servi."""
    courtier = PaperBroker(cfg)
    bougie = _bougie(2010.00, 2011.00, 1999.00, 2005.00)
    position = courtier.execute(_signal(level=2000.00, stop=1995.00), bougie, 1)
    assert position is not None
    # Servi au niveau, plus le spread : un achat limite s'exécute à l'ask.
    assert position.entry == pytest.approx(2000.00 + 12 * 0.01)


def test_ouverture_au_dela_remplit_sans_marge(cfg):
    """Une ouverture déjà sous le niveau n'a aucune file d'attente à purger."""
    courtier = PaperBroker(cfg)
    bougie = _bougie(1998.00, 2005.00, 1997.50, 2004.00)
    position = courtier.execute(_signal(level=2000.00, stop=1990.00), bougie, 1)
    assert position is not None
    # Servi à l'ouverture, meilleur prix, plus le spread.
    assert position.entry == pytest.approx(1998.00 + 12 * 0.01)


def test_vente_symetrique(cfg):
    courtier = PaperBroker(cfg)
    effleure = _bougie(1990.00, 2000.00, 1989.00, 1995.00)
    assert courtier.execute(
        _signal(2000.00, 2005.00, "bearish"), effleure, 1
    ) is None

    traverse = _bougie(1990.00, 2001.00, 1989.00, 1995.00)
    assert courtier.execute(
        _signal(2000.00, 2005.00, "bearish"), traverse, 1
    ) is not None


def test_marge_nulle_retablit_l_ancien_comportement(cfg):
    """À 0, le simple contact suffit — utile pour mesurer le biais d'avant."""
    cfg.risk.limit_fill_margin_points = 0.0
    courtier = PaperBroker(cfg)
    bougie = _bougie(2010.00, 2011.00, 2000.00, 2005.00)
    assert courtier.execute(_signal(2000.00, 1995.00), bougie, 1) is not None


def test_ordre_au_marche_non_concerne(cfg):
    """Un ordre au marché est rempli à la clôture : aucune file d'attente."""
    courtier = PaperBroker(cfg)
    signal = _signal(2005.00, 2000.00)
    signal.entry_type = "market"
    bougie = _bougie(2010.00, 2011.00, 2004.00, 2005.00)
    position = courtier.execute(signal, bougie, 1)
    assert position is not None
    assert position.entry == pytest.approx(2005.00 + 12 * 0.01)
