"""Tests du dimensionnement des positions et du calcul de P&L."""

from __future__ import annotations

import pytest

from smcbot.config import RiskConfig, SymbolSpec
from smcbot.risk import points, position_size, r_multiple, trade_pnl


@pytest.fixture
def eurusd() -> SymbolSpec:
    return SymbolSpec(
        name="EURUSD",
        digits=5,
        point=0.00001,
        value_per_point_per_lot=1.0,
        min_lot=0.01,
        lot_step=0.01,
    )


def test_conversion_en_points(eurusd):
    assert points(0.00200, eurusd) == pytest.approx(200)
    assert points(-0.00200, eurusd) == pytest.approx(200)


def test_taille_position_respecte_le_risque(eurusd):
    """1 % de 10 000 = 100 $ risqués sur un stop de 200 points à 1 $/point/lot."""
    risk = RiskConfig(risk_pct=1.0)
    lots = position_size(10_000, entry=1.10000, stop=1.09800, risk=risk, symbol=eurusd)
    assert lots == pytest.approx(0.5)

    perte = trade_pnl("bullish", 1.10000, 1.09800, lots, eurusd)
    assert perte == pytest.approx(-100.0)


def test_taille_position_arrondie_a_l_inferieur(eurusd):
    """L'arrondi ne doit jamais faire dépasser le risque cible."""
    risk = RiskConfig(risk_pct=1.0)
    lots = position_size(10_000, entry=1.10000, stop=1.09730, risk=risk, symbol=eurusd)
    # 100 / 270 = 0.370... → 0.37
    assert lots == pytest.approx(0.37)
    perte = abs(trade_pnl("bullish", 1.10000, 1.09730, lots, eurusd))
    assert perte <= 100.0


def test_volume_trop_faible_refuse(eurusd):
    """Sous le lot minimal, on ne prend pas le trade plutôt que de sur-risquer."""
    risk = RiskConfig(risk_pct=0.01)
    lots = position_size(100, entry=1.10000, stop=1.09000, risk=risk, symbol=eurusd)
    assert lots == 0.0


def test_stop_nul_refuse(eurusd):
    risk = RiskConfig(risk_pct=1.0)
    assert position_size(10_000, 1.1, 1.1, risk, eurusd) == 0.0


def test_volume_plafonne(eurusd):
    petit = SymbolSpec(point=0.00001, value_per_point_per_lot=1.0, max_lot=1.0)
    risk = RiskConfig(risk_pct=50.0)
    lots = position_size(1_000_000, 1.10000, 1.09900, risk, petit)
    assert lots == pytest.approx(1.0)


def test_pnl_vente(eurusd):
    """Une vente gagne quand le prix baisse."""
    gain = trade_pnl("bearish", 1.10000, 1.09500, 1.0, eurusd)
    assert gain == pytest.approx(500.0)


def test_commission_deduite():
    symbol = SymbolSpec(point=0.00001, value_per_point_per_lot=1.0, commission_per_lot=7.0)
    pnl = trade_pnl("bullish", 1.10000, 1.10100, 2.0, symbol)
    assert pnl == pytest.approx(100 * 1.0 * 2.0 - 7.0 * 2.0)


def test_r_multiple():
    assert r_multiple("bullish", 1.1000, 1.0980, 1.1040) == pytest.approx(2.0)
    assert r_multiple("bullish", 1.1000, 1.0980, 1.0980) == pytest.approx(-1.0)
    assert r_multiple("bearish", 1.1000, 1.1020, 1.0960) == pytest.approx(2.0)
    assert r_multiple("bearish", 1.1000, 1.1020, 1.1020) == pytest.approx(-1.0)


# ------------------------------------------------------------- frais de portage


def test_rollovers_compte_les_nuits():
    from datetime import datetime, timezone
    from smcbot.risk import rollovers

    def t(jour, heure):
        return datetime(2024, 1, jour, heure, tzinfo=timezone.utc)

    assert rollovers(t(1, 10), t(1, 20)) == 0      # même journée, avant 21h
    assert rollovers(t(1, 10), t(1, 22)) == 1      # une nuit franchie
    assert rollovers(t(1, 10), t(2, 10)) == 1
    # 2024-01-03 est un mercredi : triple, ce qui couvre le week-end
    assert rollovers(t(2, 10), t(4, 10)) == 4
    # Vendredi → lundi : le week-end n'est pas facturé en plus
    assert rollovers(t(5, 10), t(8, 10)) == 1
    assert rollovers(t(5, 10), t(5, 9)) == 0       # fin avant le début


def test_swap_cost(eurusd):
    from datetime import datetime, timezone
    from smcbot.risk import swap_cost

    symbol = SymbolSpec(
        point=0.01, value_per_point_per_lot=1.0,
        swap_long_points=-8.0, swap_short_points=2.0,
    )
    ouverture = datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
    cloture = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)   # une nuit

    assert swap_cost("bullish", 0.5, ouverture, cloture, symbol) == pytest.approx(-4.0)
    assert swap_cost("bearish", 0.5, ouverture, cloture, symbol) == pytest.approx(1.0)
    # Sans portage configuré, aucun frais
    assert swap_cost("bullish", 0.5, ouverture, cloture, eurusd) == 0.0


def test_portage_deduit_du_resultat():
    """Une position gardée plusieurs nuits paie, et ça se voit dans le P&L."""
    from datetime import datetime, timedelta, timezone
    from smcbot.broker import PaperBroker
    from smcbot.config import BotConfig
    from smcbot.data import Candle
    from smcbot.smc import BULLISH
    from test_backtest import make_signal

    cfg = BotConfig()
    cfg.symbol = SymbolSpec(
        point=0.01, value_per_point_per_lot=1.0, spread_points=0.0,
        lot_step=0.01, swap_long_points=-10.0,
    )
    cfg.risk.initial_balance = 10_000
    cfg.risk.risk_pct = 1.0
    cfg.filters.weekdays = []

    t0 = datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
    broker = PaperBroker(cfg)
    entree = Candle(t0, 2655.0, 2656.0, 2649.9, 2652.0)
    broker.on_candle(entree, 0)
    pos = broker.execute(make_signal(BULLISH, 2650.0, 2645.0), entree, 0)
    assert pos is not None

    # Sortie au take profit deux jours plus tard
    sortie = Candle(t0 + timedelta(days=2), 2652.0, 2665.0, 2651.0, 2664.0)
    broker.on_candle(sortie, 1)

    trade = broker.trades[0]
    assert trade.swap < 0, "le portage doit être un coût"
    # Lundi 10 h → mercredi 10 h : les rollovers de lundi et mardi 21 h sont
    # franchis, celui du mercredi tombe après la clôture. Deux nuits, donc.
    assert trade.swap == pytest.approx(2 * -10.0 * pos.lots)
    assert trade.pnl == pytest.approx(
        (trade.exit - trade.entry) / 0.01 * pos.lots + trade.swap
    )
