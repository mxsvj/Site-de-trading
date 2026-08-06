"""Tests du mode scalping : multi-timeframe, sessions, filtres de coût."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

from smcbot.backtest import run_backtest
from smcbot.broker import PaperBroker
from smcbot.config import (
    BotConfig,
    FilterConfig,
    SmcConfig,
    SymbolSpec,
    scalping_xauusd,
    xauusd,
)
from smcbot.data import Candle, Resampler, resample, synthetic_series
from smcbot.filters import TradeFilters, Window
from smcbot.smc import BULLISH
from smcbot.strategy import SmcStrategy

from test_backtest import make_signal  # réutilise le constructeur de signal

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def m1(i: int, o: float, h: float, l: float, c: float, vol: float = 1.0) -> Candle:
    return Candle(T0 + timedelta(minutes=i), o, h, l, c, vol)


# ------------------------------------------------------------- rééchantillonnage


def test_resampler_agrege_correctement():
    sampler = Resampler(15)
    bougies = [m1(i, 1.0 + i, 2.0 + i, 0.5 + i, 1.5 + i) for i in range(15)]

    for bougie in bougies[:-1]:
        assert sampler.push(bougie) == [], "émission prématurée"
    assert sampler.push(bougies[-1]) == []

    # C'est la première bougie du palier suivant qui clôt le précédent
    sortie = sampler.push(m1(15, 99.0, 99.0, 99.0, 99.0))
    assert len(sortie) == 1

    htf = sortie[0]
    assert htf.time == T0
    assert htf.open == pytest.approx(bougies[0].open)
    assert htf.close == pytest.approx(bougies[-1].close)
    assert htf.high == pytest.approx(max(b.high for b in bougies))
    assert htf.low == pytest.approx(min(b.low for b in bougies))
    assert htf.volume == pytest.approx(15.0)


def test_resampler_n_emet_jamais_la_bougie_en_cours():
    """La bougie supérieure en formation ne doit pas fuiter vers le moteur."""
    sampler = Resampler(15)
    for i in range(10):
        assert sampler.push(m1(i, 1.0, 1.0, 1.0, 1.0)) == []
    assert sampler.pending is not None  # visible, mais jamais renvoyée par push


def test_resampler_gere_les_trous():
    """Un saut de plusieurs paliers (week-end) ne casse pas l'agrégation."""
    sampler = Resampler(15)
    sampler.push(m1(0, 1.0, 1.0, 1.0, 1.0))
    sortie = sampler.push(m1(240, 2.0, 2.0, 2.0, 2.0))  # 4 h plus tard
    assert len(sortie) == 1
    assert sortie[0].time == T0


def test_resample_batch():
    bougies = [m1(i, 1.0, 1.0 + i, 0.5, 1.2) for i in range(60)]
    htf = resample(bougies, 15)
    assert len(htf) == 4
    assert [c.time.minute for c in htf] == [0, 15, 30, 45]


def test_resampler_refuse_une_periode_nulle():
    with pytest.raises(ValueError):
        Resampler(0)


# -------------------------------------------------------------------- sessions


def test_plage_horaire_simple():
    fenetre = Window.parse("07:00-11:00")
    assert fenetre.contains(time(7, 0))
    assert fenetre.contains(time(10, 59))
    assert not fenetre.contains(time(11, 0))  # borne de fin exclue
    assert not fenetre.contains(time(6, 59))


def test_plage_horaire_a_cheval_sur_minuit():
    fenetre = Window.parse("22:00-02:00")
    assert fenetre.contains(time(23, 30))
    assert fenetre.contains(time(1, 0))
    assert not fenetre.contains(time(12, 0))


def test_plage_horaire_invalide():
    with pytest.raises(ValueError):
        Window.parse("nawak")


def test_session_et_jours_ouvres():
    filtres = TradeFilters(
        FilterConfig(sessions=["07:00-11:00"], weekdays=[0, 1, 2, 3, 4])
    )
    lundi_8h = datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)
    lundi_3h = datetime(2024, 1, 1, 3, 0, tzinfo=timezone.utc)
    samedi_8h = datetime(2024, 1, 6, 8, 0, tzinfo=timezone.utc)

    assert filtres.session_allows(lundi_8h)
    assert not filtres.session_allows(lundi_3h)
    assert not filtres.session_allows(samedi_8h)


def test_sans_session_tout_est_permis():
    filtres = TradeFilters(FilterConfig(sessions=[], weekdays=[]))
    assert filtres.session_allows(datetime(2024, 1, 6, 3, 0, tzinfo=timezone.utc))


# ------------------------------------------------------------- filtres de coût


def test_cout_en_points_inclut_la_commission():
    symbol = SymbolSpec(
        point=0.01, value_per_point_per_lot=1.0, spread_points=25.0,
        commission_per_lot=7.0,
    )
    filtres = TradeFilters(FilterConfig(), symbol)
    assert filtres.cost_points() == pytest.approx(32.0)  # 25 de spread + 7 de commission


def test_ratio_de_cout():
    """C'est l'arithmétique qui rend le scalping difficile."""
    symbol = xauusd()  # spread 25 points
    filtres = TradeFilters(FilterConfig(), symbol)
    assert filtres.cost_ratio(500) == pytest.approx(0.05)  # stop large : 5 %
    assert filtres.cost_ratio(100) == pytest.approx(0.25)  # stop serré : 25 %
    assert filtres.cost_ratio(50) == pytest.approx(0.50)   # 50 % du risque en frais


def test_refus_stop_trop_serre():
    filtres = TradeFilters(FilterConfig(min_stop_points=60), xauusd())
    refus = filtres.check_trade(40)
    assert refus is not None and refus.code == "stop trop serré"
    assert filtres.check_trade(80) is None


def test_refus_frais_trop_eleves():
    filtres = TradeFilters(FilterConfig(max_cost_ratio=0.30), xauusd())
    refus = filtres.check_trade(50)  # 25/50 = 50 %
    assert refus is not None and refus.code.startswith("frais")
    assert filtres.check_trade(200) is None  # 12,5 %


def test_refus_spread_trop_large():
    filtres = TradeFilters(FilterConfig(max_spread_points=40), xauusd())
    refus = filtres.check_trade(500, spread_points=80)
    assert refus is not None and refus.code == "spread trop large"
    assert filtres.check_trade(500, spread_points=20) is None


def test_filtres_desactives_par_defaut():
    filtres = TradeFilters(FilterConfig(), xauusd())
    assert filtres.check_trade(1) is None


# ----------------------------------------------------- application au courtier


def scalp_config(**filtres) -> BotConfig:
    cfg = BotConfig()
    cfg.symbol = SymbolSpec(
        point=0.01, value_per_point_per_lot=1.0, spread_points=0.0, lot_step=0.01
    )
    cfg.risk.initial_balance = 10_000
    cfg.risk.risk_pct = 1.0
    cfg.filters = FilterConfig(weekdays=[], **filtres)
    return cfg


def test_courtier_bloque_hors_session():
    broker = PaperBroker(scalp_config(sessions=["07:00-11:00"]))

    nuit = Candle(T0.replace(hour=3), 2650.0, 2651.0, 2649.0, 2650.5)
    broker.on_candle(nuit, 0)
    assert not broker.can_open

    matin = Candle(T0.replace(hour=8), 2650.0, 2651.0, 2649.0, 2650.5)
    broker.on_candle(matin, 1)
    assert broker.can_open


def test_plafond_de_trades_par_jour():
    broker = PaperBroker(scalp_config(max_trades_per_day=1))

    bar = Candle(T0.replace(hour=8), 2655.0, 2656.0, 2649.9, 2652.0)
    broker.on_candle(bar, 0)
    assert broker.execute(make_signal(BULLISH, 2650.0, 2645.0), bar, 0) is not None

    # On solde la position pour isoler le plafond quotidien de `max_concurrent`
    broker.on_candle(Candle(T0.replace(hour=9), 2650.0, 2651.0, 2640.0, 2644.0), 1)
    assert not broker.positions
    assert not broker.can_open, "le plafond quotidien devrait bloquer"

    # Le lendemain, le compteur repart
    demain = Candle(T0 + timedelta(days=1, hours=8), 2650.0, 2651.0, 2649.0, 2650.5)
    broker.on_candle(demain, 2)
    assert broker.can_open


def test_courtier_refuse_un_stop_trop_serre():
    broker = PaperBroker(scalp_config(min_stop_points=100))
    bar = Candle(T0.replace(hour=8), 2655.0, 2656.0, 2649.9, 2652.0)
    broker.on_candle(bar, 0)

    # 50 points seulement entre l'entrée et le stop
    assert broker.execute(make_signal(BULLISH, 2650.0, 2649.5), bar, 0) is None
    assert broker.skipped.get("stop trop serré") == 1


def test_courtier_refuse_des_frais_excessifs():
    cfg = scalp_config(max_cost_ratio=0.20)
    cfg.symbol.spread_points = 25.0
    broker = PaperBroker(cfg)

    bar = Candle(T0.replace(hour=8), 2655.0, 2656.0, 2649.9, 2652.0)
    broker.on_candle(bar, 0)

    # Stop à 50 points → 25/50 = 50 % de frais, au-dessus du plafond de 20 %
    assert broker.execute(make_signal(BULLISH, 2650.0, 2649.5), bar, 0) is None
    assert any(code.startswith("frais") for code in broker.skipped)


# ------------------------------------------------------------ multi-timeframe


def test_biais_provient_de_l_unite_superieure():
    cfg = BotConfig(timeframe="M1", htf="M15")
    strat = SmcStrategy(cfg)
    assert strat.htf_engine is not None

    for candle in synthetic_series(2000, point=0.01, timeframe_minutes=1, seed=5):
        strat.on_candle(candle, can_open=False)

    assert strat.htf_engine.structure_events, "aucune structure sur l'unité supérieure"
    assert strat.bias == strat.htf_engine.bias
    # Les deux moteurs voient des séries de tailles très différentes
    assert len(strat.htf_engine.candles) == pytest.approx(2000 / 15, abs=2)


def test_unite_superieure_doit_etre_plus_grande():
    with pytest.raises(ValueError):
        SmcStrategy(BotConfig(timeframe="M15", htf="M5"))
    with pytest.raises(ValueError):
        SmcStrategy(BotConfig(timeframe="M1", htf="M1"))


def test_unite_superieure_inconnue():
    with pytest.raises(ValueError):
        SmcStrategy(BotConfig(timeframe="M1", htf="M7"))


def test_sans_htf_le_biais_reste_local():
    strat = SmcStrategy(BotConfig())
    assert strat.htf_engine is None
    for candle in synthetic_series(600, seed=5):
        strat.on_candle(candle, can_open=False)
    assert strat.bias == strat.engine.bias


def test_confluence_htf_reduit_le_nombre_de_trades():
    candles = synthetic_series(15_000, start=2650.0, point=0.01,
                               timeframe_minutes=1, seed=8)

    souple = BotConfig(timeframe="M1", htf="M15", symbol=xauusd())
    souple.symbol.spread_points = 0.0
    souple.smc = SmcConfig(swing_lookback=2, require_htf_zone=False)

    strict = BotConfig(timeframe="M1", htf="M15", symbol=xauusd())
    strict.symbol.spread_points = 0.0
    strict.smc = SmcConfig(swing_lookback=2, require_htf_zone=True)

    sans = run_backtest(candles, souple)
    avec = run_backtest(candles, strict)
    assert avec.report.trades <= sans.report.trades


def test_absence_de_lookahead_en_multi_timeframe(scalp_candles):
    """Le rééchantillonnage ne doit pas laisser fuiter le futur."""
    cfg = scalping_xauusd()
    cfg.filters.sessions = []
    cfg.filters.weekdays = []

    complet = run_backtest(scalp_candles, cfg)
    partiel = run_backtest(scalp_candles[:8000], cfg)

    attendus = [t for t in complet.trades if t.close_index < 7999]
    obtenus = [t for t in partiel.trades if t.exit_reason != "fin de série"]

    assert obtenus, "le préfixe n'a produit aucun trade"
    assert len(obtenus) == len(attendus)
    for a, b in zip(attendus, obtenus):
        assert a.open_time == b.open_time
        assert a.entry == pytest.approx(b.entry)
        assert a.pnl == pytest.approx(b.pnl)


@pytest.fixture(scope="module")
def scalp_candles():
    return synthetic_series(16_000, start=2650.0, point=0.01,
                            timeframe_minutes=1, seed=21)


# ---------------------------------------------------------------- preset XAUUSD


def test_preset_scalping_est_coherent():
    cfg = scalping_xauusd()
    assert cfg.timeframe == "M1" and cfg.htf == "M15"
    assert cfg.symbol.name == "XAUUSD"
    assert cfg.symbol.point == 0.01
    # 100 onces × 0,01 $ = 1 $ par point et par lot
    assert cfg.symbol.value_per_point_per_lot == pytest.approx(1.0)
    assert cfg.filters.sessions and cfg.filters.max_cost_ratio > 0

    # Le plancher de stop doit rendre le plafond de frais atteignable
    filtres = TradeFilters(cfg.filters, cfg.symbol)
    assert filtres.cost_ratio(cfg.filters.min_stop_points) <= cfg.filters.max_cost_ratio


def test_preset_scalping_tourne_de_bout_en_bout(scalp_candles):
    result = run_backtest(scalp_candles, scalping_xauusd())
    assert result.candles == 16_000
    # Les filtres doivent avoir écarté des setups, sinon ils ne servent à rien
    assert sum(result.skipped.values()) > 0
    for trade in result.trades:
        distance = abs(trade.entry - trade.stop) / 0.01
        assert distance >= scalping_xauusd().filters.min_stop_points - 1e-6


def test_sessions_limitent_les_heures_de_trade(scalp_candles):
    cfg = scalping_xauusd()
    cfg.filters.sessions = ["07:00-11:00"]
    result = run_backtest(scalp_candles, cfg)

    assert result.trades, "aucun trade : le test ne prouverait rien"
    for trade in result.trades:
        assert 7 <= trade.open_time.hour < 11, f"trade hors session : {trade.open_time}"


# -------------------------------------------------------------- configuration


def test_config_json_conserve_filtres_et_htf(tmp_path):
    chemin = tmp_path / "scalp.json"
    scalping_xauusd().to_json(chemin)
    relu = BotConfig.from_json(chemin)

    origine = scalping_xauusd()
    assert relu.htf == origine.htf
    assert relu.timeframe == origine.timeframe
    assert relu.filters.sessions == origine.filters.sessions
    assert relu.filters.max_cost_ratio == origine.filters.max_cost_ratio
    assert relu.htf_smc.swing_lookback == origine.htf_smc.swing_lookback
    assert relu.symbol.point == origine.symbol.point
