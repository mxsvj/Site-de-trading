"""Tests du moteur SMC : swings, structure, order blocks, FVG, liquidité."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from smcbot.config import SmcConfig, SymbolSpec
from smcbot.data import Candle
from smcbot.smc import BEARISH, BULLISH, SmcEngine

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(T0 + timedelta(minutes=15 * i), o, h, l, c)


def series(specs: list[tuple[float, float, float, float]]) -> list[Candle]:
    return [candle(i, *s) for i, s in enumerate(specs)]


def feed(engine: SmcEngine, candles: list[Candle]) -> list:
    return [engine.push(c) for c in candles]


# ------------------------------------------------------------------- swings


def test_swing_high_confirme_avec_retard():
    """Un swing n'est visible qu'après `swing_lookback` bougies : pas de lookahead."""
    engine = SmcEngine(SmcConfig(swing_lookback=2))
    candles = series(
        [
            (1.0, 1.1, 0.9, 1.0),  # 0
            (1.0, 1.2, 1.0, 1.1),  # 1
            (1.1, 1.5, 1.1, 1.4),  # 2  ← sommet
            (1.4, 1.3, 1.0, 1.1),  # 3
            (1.1, 1.2, 0.9, 1.0),  # 4  ← confirme le swing en 2
        ]
    )
    events = feed(engine, candles)

    assert engine.swing_highs, "aucun swing détecté"
    assert engine.swing_highs[0].index == 2
    assert engine.swing_highs[0].price == pytest.approx(1.5)
    # Aucun swing connu avant la bougie 4
    assert all(len(e.mitigated) == 0 for e in events[:4])


def test_swing_low_detecte():
    engine = SmcEngine(SmcConfig(swing_lookback=2))
    feed(
        engine,
        series(
            [
                (1.5, 1.6, 1.4, 1.5),
                (1.5, 1.5, 1.3, 1.4),
                (1.4, 1.4, 1.0, 1.2),  # ← creux
                (1.2, 1.4, 1.2, 1.3),
                (1.3, 1.5, 1.25, 1.45),
            ]
        ),
    )
    assert engine.swing_lows
    assert engine.swing_lows[0].index == 2
    assert engine.swing_lows[0].price == pytest.approx(1.0)


# ---------------------------------------------------------------- structure


def test_choch_puis_bos_haussier():
    """Première cassure = CHoCH, les suivantes dans le même sens = BOS."""
    engine = SmcEngine(SmcConfig(swing_lookback=1))
    candles = series(
        [
            (1.00, 1.05, 0.95, 1.00),
            (1.00, 1.20, 0.99, 1.15),  # sommet local
            (1.15, 1.16, 1.05, 1.06),
            (1.06, 1.30, 1.05, 1.28),  # casse 1.20 → CHOCH haussier
            (1.28, 1.29, 1.20, 1.22),
            (1.22, 1.45, 1.21, 1.40),  # casse le nouveau sommet → BOS
        ]
    )
    feed(engine, candles)

    kinds = [(e.kind, e.direction) for e in engine.structure_events]
    assert ("CHOCH", BULLISH) in kinds
    assert kinds[0] == ("CHOCH", BULLISH)
    assert engine.bias == BULLISH
    assert any(k == ("BOS", BULLISH) for k in kinds)


def test_cassure_unique_par_swing():
    """Un même swing ne peut pas déclencher deux cassures consécutives."""
    engine = SmcEngine(SmcConfig(swing_lookback=1))
    feed(
        engine,
        series(
            [
                (1.00, 1.10, 0.95, 1.00),
                (1.00, 1.20, 0.99, 1.15),
                (1.15, 1.16, 1.05, 1.06),
                (1.06, 1.30, 1.05, 1.28),  # cassure
                (1.28, 1.32, 1.27, 1.31),  # au-dessus, mais plus de swing actif
                (1.31, 1.33, 1.30, 1.32),
            ]
        ),
    )
    haussieres = [e for e in engine.structure_events if e.direction == BULLISH]
    assert len(haussieres) == 1


def test_biais_baissier():
    engine = SmcEngine(SmcConfig(swing_lookback=1))
    feed(
        engine,
        series(
            [
                (1.50, 1.55, 1.45, 1.50),
                (1.50, 1.52, 1.30, 1.35),  # creux
                (1.35, 1.45, 1.34, 1.44),
                (1.44, 1.45, 1.20, 1.25),  # casse 1.30 → CHOCH baissier
            ]
        ),
    )
    assert engine.bias == BEARISH
    assert engine.structure_events[0].kind == "CHOCH"


# ---------------------------------------------------------------------- FVG


def test_fvg_haussier():
    """Gap entre le sommet de i-2 et le creux de i."""
    engine = SmcEngine(SmcConfig(swing_lookback=2))
    feed(
        engine,
        series(
            [
                (1.00, 1.05, 0.98, 1.02),  # sommet 1.05
                (1.02, 1.20, 1.01, 1.18),  # impulsion
                (1.18, 1.25, 1.10, 1.22),  # creux 1.10 > 1.05 → FVG
            ]
        ),
    )
    gaps = [g for g in engine.fvgs if g.direction == BULLISH]
    assert len(gaps) == 1
    assert gaps[0].bottom == pytest.approx(1.05)
    assert gaps[0].top == pytest.approx(1.10)


def test_fvg_baissier_et_remplissage():
    engine = SmcEngine(SmcConfig(swing_lookback=2))
    feed(
        engine,
        series(
            [
                (1.20, 1.22, 1.18, 1.19),  # creux 1.18
                (1.19, 1.19, 1.05, 1.06),
                (1.06, 1.10, 1.02, 1.04),  # sommet 1.10 < 1.18 → FVG baissier
            ]
        ),
    )
    gaps = [g for g in engine.fvgs if g.direction == BEARISH]
    assert len(gaps) == 1
    assert gaps[0].bottom == pytest.approx(1.10)
    assert gaps[0].top == pytest.approx(1.18)
    assert not gaps[0].filled

    engine.push(candle(3, 1.04, 1.19, 1.03, 1.15))  # remonte dans le gap
    assert gaps[0].filled


def test_fvg_taille_minimale():
    """Un gap plus petit que le seuil est ignoré."""
    symbol = SymbolSpec(point=0.00001)
    engine = SmcEngine(SmcConfig(fvg_min_points=100), symbol)  # seuil = 0.001
    feed(
        engine,
        series(
            [
                (1.0000, 1.0005, 0.9998, 1.0002),
                (1.0002, 1.0020, 1.0001, 1.0018),
                (1.0018, 1.0025, 1.0010, 1.0022),  # gap de 0.0005 seulement
            ]
        ),
    )
    assert not engine.fvgs


# --------------------------------------------------------------- order block


def test_order_block_est_la_derniere_bougie_opposee():
    engine = SmcEngine(SmcConfig(swing_lookback=1, ob_lookback=10))
    candles = series(
        [
            (1.00, 1.10, 0.95, 1.00),
            (1.00, 1.20, 0.99, 1.15),  # sommet de référence
            (1.15, 1.16, 1.05, 1.06),  # bougie baissière ← l'order block
            (1.06, 1.30, 1.05, 1.28),  # impulsion qui casse 1.20
        ]
    )
    events = feed(engine, candles)
    ob = events[3].new_order_block

    assert ob is not None
    assert ob.index == 2
    assert ob.direction == BULLISH
    assert ob.bottom == pytest.approx(1.05)
    assert ob.top == pytest.approx(1.16)
    assert ob.break_level == pytest.approx(1.20)


def test_order_block_sur_corps():
    engine = SmcEngine(SmcConfig(swing_lookback=1, ob_use_body=True))
    events = feed(
        engine,
        series(
            [
                (1.00, 1.10, 0.95, 1.00),
                (1.00, 1.20, 0.99, 1.15),
                (1.15, 1.16, 1.05, 1.06),  # corps = 1.06 → 1.15
                (1.06, 1.30, 1.05, 1.28),
            ]
        ),
    )
    ob = events[3].new_order_block
    assert ob is not None
    assert ob.bottom == pytest.approx(1.06)
    assert ob.top == pytest.approx(1.15)


def test_mitigation_puis_invalidation():
    engine = SmcEngine(SmcConfig(swing_lookback=1))
    events = feed(
        engine,
        series(
            [
                (1.00, 1.10, 0.95, 1.00),
                (1.00, 1.20, 0.99, 1.15),
                (1.15, 1.16, 1.05, 1.06),
                (1.06, 1.30, 1.05, 1.28),
            ]
        ),
    )
    ob = events[3].new_order_block
    assert ob is not None and not ob.mitigated

    # Retour dans la zone : mitigation signalée
    ev = engine.push(candle(4, 1.28, 1.29, 1.12, 1.20))
    assert ob.mitigated
    assert ob in ev.mitigated

    # Clôture sous la zone : invalidation
    engine.push(candle(5, 1.20, 1.21, 1.00, 1.01))
    assert ob.invalidated


def test_order_block_pas_mitige_par_sa_propre_bougie_de_cassure():
    """La bougie qui crée l'OB ne doit pas le marquer comme déjà touché."""
    engine = SmcEngine(SmcConfig(swing_lookback=1))
    events = feed(
        engine,
        series(
            [
                (1.00, 1.10, 0.95, 1.00),
                (1.00, 1.20, 0.99, 1.15),
                (1.15, 1.16, 1.05, 1.06),
                (1.06, 1.30, 1.05, 1.28),  # traverse la zone de l'OB
            ]
        ),
    )
    ob = events[3].new_order_block
    assert ob is not None
    assert not ob.mitigated
    assert events[3].mitigated == []


def test_confluence_fvg_detectee():
    engine = SmcEngine(SmcConfig(swing_lookback=1, ob_lookback=10))
    events = feed(
        engine,
        series(
            [
                (1.00, 1.10, 0.95, 1.00),
                (1.00, 1.20, 0.99, 1.15),
                (1.15, 1.16, 1.05, 1.06),  # order block
                (1.06, 1.18, 1.06, 1.17),
                (1.17, 1.40, 1.25, 1.38),  # creux 1.25 > sommet 1.16 → FVG
            ]
        ),
    )
    ob = events[4].new_order_block
    assert ob is not None
    assert ob.has_fvg


# --------------------------------------------------------------- liquidité


def test_sweep_haussier():
    """Mèche sous un ancien creux, clôture au-dessus : liquidité prise."""
    engine = SmcEngine(SmcConfig(swing_lookback=1, sweep_lookback=20))
    events = feed(
        engine,
        series(
            [
                (1.20, 1.25, 1.15, 1.22),
                (1.22, 1.23, 1.10, 1.12),  # creux 1.10
                (1.12, 1.30, 1.11, 1.28),
                (1.28, 1.29, 1.05, 1.26),  # balaie 1.10 puis clôture au-dessus
            ]
        ),
    )
    sweep = events[3].sweep
    assert sweep is not None
    assert sweep.direction == BULLISH
    assert sweep.level == pytest.approx(1.10)


def test_sweep_baissier():
    engine = SmcEngine(SmcConfig(swing_lookback=1, sweep_lookback=20))
    events = feed(
        engine,
        series(
            [
                (1.00, 1.05, 0.95, 1.00),
                (1.00, 1.30, 0.99, 1.28),  # sommet 1.30
                (1.28, 1.29, 1.15, 1.16),
                (1.16, 1.40, 1.14, 1.20),  # dépasse 1.30 mais clôture en dessous
            ]
        ),
    )
    sweep = events[3].sweep
    assert sweep is not None
    assert sweep.direction == BEARISH
    assert sweep.level == pytest.approx(1.30)


# ------------------------------------------------------------- robustesse


def test_pas_de_plantage_sur_serie_plate():
    engine = SmcEngine()
    for i in range(50):
        engine.push(candle(i, 1.0, 1.0, 1.0, 1.0))
    assert engine.bias is None
    assert not engine.order_blocks


def test_serie_synthetique_produit_des_evenements():
    from smcbot.data import synthetic_series

    engine = SmcEngine()
    for c in synthetic_series(1500, seed=3):
        engine.push(c)

    assert len(engine.swing_highs) > 20
    assert len(engine.structure_events) > 10
    assert len(engine.order_blocks) > 5
    assert any(f.direction == BULLISH for f in engine.fvgs)
    assert any(f.direction == BEARISH for f in engine.fvgs)


def test_purge_des_zones_anciennes():
    """La mémoire ne croît pas indéfiniment en exécution continue."""
    from smcbot.data import synthetic_series

    engine = SmcEngine(SmcConfig(ob_max_age=20))
    for c in synthetic_series(4000, seed=11):
        engine.push(c)
    assert len(engine.order_blocks) <= 600
    assert len(engine.fvgs) <= 600
