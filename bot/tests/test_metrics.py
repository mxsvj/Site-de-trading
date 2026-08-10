"""Tests de l'efficacité du risque : l'écart entre risque visé et risque pris.

Le volume est tronqué au pas du courtier, donc le risque réel est toujours
inférieur ou égal au risque visé. `expectancy_r` ne peut pas le voir, puisque
`r_multiple()` ne dépend que de distances de prix. Ces tests verrouillent la
mesure qui le rend visible.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from smcbot.broker import Trade
from smcbot.config import SymbolSpec
from smcbot.metrics import build_report


@pytest.fixture
def xauusd() -> SymbolSpec:
    """L'or chez le courtier de référence : 0,8652 € le point pour 1 lot."""
    return SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        value_per_point_per_lot=0.8652,
        min_lot=0.01,
        lot_step=0.01,
    )


def _trade(entry: float, stop: float, lots: float, balance_avant: float) -> Trade:
    """Trade clôturé à somme nulle, pour isoler le dimensionnement."""
    return Trade(
        direction="bullish",
        entry=entry,
        exit=entry,
        stop=stop,
        take_profit=entry + 1.0,
        lots=lots,
        open_time=datetime(2026, 1, 1, 9, 0),
        close_time=datetime(2026, 1, 1, 9, 30),
        open_index=0,
        close_index=1,
        pnl=0.0,
        swap=0.0,
        r=0.0,
        exit_reason="tp",
        reason="",
        balance_after=balance_avant,
    )


def test_stop_efficace_utilise_tout_le_risque(xauusd):
    """289,0 points : le volume exact vaut 0,02 lot pile, rien n'est perdu."""
    # 5 € / (288,97 points × 0,8652) = 0,02 lot exactement.
    trade = _trade(entry=2000.00, stop=2000.00 - 2.8897, lots=0.02,
                   balance_avant=1000.0)
    report = build_report([trade], [], 1000.0, symbol=xauusd, risk_pct=0.5)
    assert report.risk_efficiency == pytest.approx(1.0, abs=0.005)


def test_stop_juste_trop_large_perd_la_moitie_du_risque(xauusd):
    """289,0 points au lieu de 288,97 : le volume tombe à 0,01 lot.

    C'est le cœur du problème — trois centièmes de point coûtent la moitié du
    risque, parce que les volumes efficaces sont des lames de rasoir.
    """
    trade = _trade(entry=2000.00, stop=2000.00 - 2.89, lots=0.01,
                   balance_avant=1000.0)
    report = build_report([trade], [], 1000.0, symbol=xauusd, risk_pct=0.5)
    assert report.risk_efficiency == pytest.approx(0.5, abs=0.01)


def test_mesure_absente_sans_specification(xauusd):
    """Sans symbole ni risque, le rapport reste muet plutôt que faux."""
    trade = _trade(entry=2000.00, stop=1997.11, lots=0.01, balance_avant=1000.0)
    report = build_report([trade], [], 1000.0)
    assert report.risk_efficiency == 0.0


def test_capital_reconstitue_avant_le_trade(xauusd):
    """Le risque se juge sur le capital d'avant le trade, pas d'après.

    `Trade` ne porte que `balance_after`. Prendre celui-ci tel quel gonflerait
    ou minorerait le risque visé dès que le compte a bougé.
    """
    trade = _trade(entry=2000.00, stop=2000.00 - 2.8897, lots=0.02,
                   balance_avant=1000.0)
    trade.pnl = 500.0
    trade.balance_after = 1500.0  # le capital valait 1 000 avant le trade
    report = build_report([trade], [], 1000.0, symbol=xauusd, risk_pct=0.5)
    assert report.risk_efficiency == pytest.approx(1.0, abs=0.005)
