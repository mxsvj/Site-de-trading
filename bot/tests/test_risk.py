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
