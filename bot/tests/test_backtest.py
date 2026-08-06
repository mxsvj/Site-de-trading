"""Tests d'exécution : courtier simulé, backtest, paper trading."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from smcbot.backtest import run_backtest
from smcbot.broker import PaperBroker
from smcbot.config import BotConfig, RiskConfig, SmcConfig, SymbolSpec
from smcbot.data import Candle, ReplayFeed, synthetic_series
from smcbot.paper import PaperTrader
from smcbot.smc import BEARISH, BULLISH, OrderBlock
from smcbot.strategy import Signal

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(T0 + timedelta(minutes=15 * i), o, h, l, c)


def make_config(**risk_kwargs) -> BotConfig:
    cfg = BotConfig()
    cfg.symbol = SymbolSpec(
        point=0.00001, value_per_point_per_lot=1.0, spread_points=0.0, lot_step=0.01
    )
    cfg.risk = RiskConfig(initial_balance=10_000, risk_pct=1.0, tp_r=2.0, **risk_kwargs)
    return cfg


def make_signal(
    direction: str, entry: float, stop: float, tp_r: float = 2.0, index: int = 1
) -> Signal:
    ob = OrderBlock(
        index=0,
        direction=direction,
        top=entry,
        bottom=stop,
        created_at=0,
        break_level=entry,
        target=entry,
    )
    return Signal(
        index=index,
        time=T0,
        direction=direction,
        entry_level=entry,
        stop=stop,
        tp_r=tp_r,
        reason="test",
        order_block=ob,
    )


# ------------------------------------------------------------------ courtier


def test_achat_touche_le_take_profit():
    cfg = make_config()
    broker = PaperBroker(cfg)

    entry_bar = candle(1, 1.10500, 1.10600, 1.09990, 1.10200)
    broker.on_candle(entry_bar, 1)
    pos = broker.execute(make_signal(BULLISH, 1.10000, 1.09800), entry_bar, 1)

    assert pos is not None
    assert pos.entry == pytest.approx(1.10000)
    assert pos.take_profit == pytest.approx(1.10400)  # 2 R sur 200 points
    assert pos.lots == pytest.approx(0.5)  # 1 % de 10 000 sur 200 points

    broker.on_candle(candle(2, 1.10200, 1.10500, 1.10100, 1.10450), 2)
    assert len(broker.trades) == 1
    trade = broker.trades[0]
    assert trade.exit_reason == "TP"
    assert trade.r == pytest.approx(2.0)
    assert trade.pnl == pytest.approx(200.0)
    assert broker.balance == pytest.approx(10_200.0)


def test_achat_touche_le_stop():
    cfg = make_config()
    broker = PaperBroker(cfg)
    entry_bar = candle(1, 1.10500, 1.10600, 1.09990, 1.10200)
    broker.on_candle(entry_bar, 1)
    broker.execute(make_signal(BULLISH, 1.10000, 1.09800), entry_bar, 1)

    broker.on_candle(candle(2, 1.10100, 1.10150, 1.09700, 1.09750), 2)
    trade = broker.trades[0]
    assert trade.exit_reason == "SL"
    assert trade.r == pytest.approx(-1.0)
    assert trade.pnl == pytest.approx(-100.0)  # exactement le risque prévu


def test_stop_prioritaire_si_les_deux_niveaux_sont_dans_la_bougie():
    """Hypothèse conservatrice : on ne suppose jamais le meilleur cas."""
    cfg = make_config()
    broker = PaperBroker(cfg)
    entry_bar = candle(1, 1.10500, 1.10600, 1.09990, 1.10200)
    broker.on_candle(entry_bar, 1)
    broker.execute(make_signal(BULLISH, 1.10000, 1.09800), entry_bar, 1)

    broker.on_candle(candle(2, 1.10100, 1.10900, 1.09700, 1.10800), 2)
    assert broker.trades[0].exit_reason == "SL"


def test_stop_possible_sur_la_bougie_d_entree():
    """Une position peut être stoppée sur la bougie qui l'a ouverte."""
    cfg = make_config()
    broker = PaperBroker(cfg)
    entry_bar = candle(1, 1.10500, 1.10600, 1.09700, 1.09750)
    broker.on_candle(entry_bar, 1)
    broker.execute(make_signal(BULLISH, 1.10000, 1.09800), entry_bar, 1)

    assert len(broker.trades) == 1
    assert broker.trades[0].exit_reason == "SL"
    assert not broker.positions


def test_vente_touche_le_take_profit():
    cfg = make_config()
    broker = PaperBroker(cfg)
    entry_bar = candle(1, 1.09500, 1.10100, 1.09400, 1.09800)
    broker.on_candle(entry_bar, 1)
    pos = broker.execute(make_signal(BEARISH, 1.10000, 1.10200), entry_bar, 1)

    assert pos is not None
    assert pos.take_profit == pytest.approx(1.09600)
    broker.on_candle(candle(2, 1.09800, 1.09850, 1.09500, 1.09550), 2)

    trade = broker.trades[0]
    assert trade.exit_reason == "TP"
    assert trade.r == pytest.approx(2.0)
    assert trade.pnl > 0


def test_spread_impute_a_l_achat():
    """Le spread renchérit l'entrée et élargit donc le risque réel."""
    cfg = make_config()
    cfg.symbol.spread_points = 20.0  # 0.00020
    broker = PaperBroker(cfg)

    entry_bar = candle(1, 1.10500, 1.10600, 1.09990, 1.10200)
    broker.on_candle(entry_bar, 1)
    pos = broker.execute(make_signal(BULLISH, 1.10000, 1.09800), entry_bar, 1)

    assert pos is not None
    assert pos.entry == pytest.approx(1.10020)  # exécuté à l'ask
    # Le risque passe de 200 à 220 points, le volume baisse en conséquence
    assert pos.lots == pytest.approx(0.45)


def test_spread_impute_a_la_vente():
    cfg = make_config()
    cfg.symbol.spread_points = 20.0
    broker = PaperBroker(cfg)

    entry_bar = candle(1, 1.09500, 1.10100, 1.09400, 1.09800)
    broker.on_candle(entry_bar, 1)
    pos = broker.execute(make_signal(BEARISH, 1.10000, 1.10200), entry_bar, 1)

    assert pos is not None
    assert pos.entry == pytest.approx(1.10000)  # vente exécutée au bid
    # Le stop se déclenche sur l'ask : le haut de bougie + spread suffit
    broker.on_candle(candle(2, 1.09900, 1.10190, 1.09800, 1.10000), 2)
    assert broker.trades[0].exit_reason == "SL"


def test_commission_reduit_le_gain():
    cfg = make_config()
    cfg.symbol.commission_per_lot = 10.0
    broker = PaperBroker(cfg)
    entry_bar = candle(1, 1.10500, 1.10600, 1.09990, 1.10200)
    broker.on_candle(entry_bar, 1)
    broker.execute(make_signal(BULLISH, 1.10000, 1.09800), entry_bar, 1)
    broker.on_candle(candle(2, 1.10200, 1.10500, 1.10100, 1.10450), 2)

    assert broker.trades[0].pnl == pytest.approx(200.0 - 10.0 * 0.5)


def test_une_seule_position_a_la_fois():
    cfg = make_config(max_concurrent=1)
    broker = PaperBroker(cfg)
    bar = candle(1, 1.10500, 1.10600, 1.09990, 1.10200)
    broker.on_candle(bar, 1)
    broker.execute(make_signal(BULLISH, 1.10000, 1.09800), bar, 1)

    assert not broker.can_open
    assert broker.execute(make_signal(BULLISH, 1.10000, 1.09800), bar, 1) is None


def test_breakeven():
    cfg = make_config(breakeven_at_r=1.0)
    broker = PaperBroker(cfg)
    entry_bar = candle(1, 1.10500, 1.10600, 1.09990, 1.10200)
    broker.on_candle(entry_bar, 1)
    pos = broker.execute(make_signal(BULLISH, 1.10000, 1.09800), entry_bar, 1)
    assert pos is not None

    # Atteint 1 R sans toucher le TP : le stop remonte à l'entrée
    broker.on_candle(candle(2, 1.10100, 1.10250, 1.10050, 1.10200), 2)
    assert pos.stop == pytest.approx(1.10000)
    assert pos.breakeven_done

    # Redescend : sortie à l'entrée, perte nulle
    broker.on_candle(candle(3, 1.10100, 1.10150, 1.09500, 1.09600), 3)
    assert broker.trades[0].pnl == pytest.approx(0.0)
    assert broker.trades[0].exit_reason == "SL"


def test_kill_switch_journalier():
    cfg = make_config(max_daily_loss_pct=1.5, max_concurrent=1)
    broker = PaperBroker(cfg)

    # Deux pertes de 1 % dans la même journée
    for i in (1, 3):
        bar = candle(i, 1.10500, 1.10600, 1.09990, 1.10200)
        broker.on_candle(bar, i)
        broker.execute(make_signal(BULLISH, 1.10000, 1.09800), bar, i)
        broker.on_candle(candle(i + 1, 1.10100, 1.10150, 1.09700, 1.09750), i + 1)

    assert len(broker.trades) == 2
    assert not broker.can_open  # journée verrouillée

    # Le lendemain, le compteur repart
    demain = Candle(T0 + timedelta(days=1), 1.10, 1.10, 1.10, 1.10)
    broker.on_candle(demain, 10)
    assert broker.can_open


def test_signal_refuse_si_volume_nul():
    cfg = make_config()
    cfg.risk.risk_pct = 0.0001
    broker = PaperBroker(cfg)
    bar = candle(1, 1.10500, 1.10600, 1.09990, 1.10200)
    broker.on_candle(bar, 1)

    assert broker.execute(make_signal(BULLISH, 1.10000, 1.09800), bar, 1) is None
    assert broker.rejected


# ------------------------------------------------------------------ backtest


@pytest.fixture(scope="module")
def demo_candles():
    return synthetic_series(3000, seed=42)


def test_backtest_produit_des_trades_coherents(demo_candles):
    cfg = BotConfig()
    result = run_backtest(demo_candles, cfg)

    assert result.candles == 3000
    assert result.trades, "la stratégie n'a produit aucun trade"
    assert result.report.trades == len(result.trades)
    assert result.report.wins + result.report.losses + result.report.breakeven == (
        result.report.trades
    )
    assert 0.0 <= result.report.winrate <= 100.0
    assert result.report.max_drawdown >= 0.0

    # Le solde final doit correspondre à la somme des P&L
    total = sum(t.pnl for t in result.trades)
    assert result.report.final_balance == pytest.approx(
        cfg.risk.initial_balance + total
    )


def test_aucune_perte_ne_depasse_le_risque_prevu(demo_candles):
    """Le dimensionnement doit borner la perte de chaque trade."""
    cfg = BotConfig()
    cfg.risk.risk_pct = 1.0
    cfg.risk.breakeven_at_r = 0.0
    result = run_backtest(demo_candles, cfg)

    for trade in result.trades:
        if trade.exit_reason == "SL":
            # -1 R exactement, à l'arrondi du volume près
            assert trade.r == pytest.approx(-1.0, abs=1e-6)


def test_absence_de_lookahead(demo_candles):
    """Rejouer un préfixe doit donner exactement les mêmes trades.

    Si la stratégie utilisait une information future, tronquer la série
    changerait les décisions passées.
    """
    cfg = BotConfig()
    complet = run_backtest(demo_candles, cfg)
    partiel = run_backtest(demo_candles[:1500], cfg)

    attendus = [t for t in complet.trades if t.close_index < 1499]
    obtenus = [t for t in partiel.trades if t.exit_reason != "fin de série"]

    assert obtenus, "le préfixe n'a produit aucun trade exploitable"
    assert len(obtenus) == len(attendus)
    for a, b in zip(attendus, obtenus):
        assert a.open_time == b.open_time
        assert a.entry == pytest.approx(b.entry)
        assert a.exit == pytest.approx(b.exit)
        assert a.pnl == pytest.approx(b.pnl)


def test_filtres_reduisent_le_nombre_de_trades(demo_candles):
    base = BotConfig()
    base.smc = SmcConfig(require_fvg=False, require_sweep=False)
    souple = run_backtest(demo_candles, base)

    strict_cfg = BotConfig()
    strict_cfg.smc = SmcConfig(require_fvg=True, require_sweep=True)
    strict = run_backtest(demo_candles, strict_cfg)

    assert strict.report.trades <= souple.report.trades


def test_positions_ouvertes_cloturees_en_fin_de_serie(demo_candles):
    result = run_backtest(demo_candles, BotConfig())
    assert all(t.close_index <= len(demo_candles) - 1 for t in result.trades)


def test_export_csv(tmp_path, demo_candles):
    result = run_backtest(demo_candles[:1200], BotConfig())
    trades_csv = tmp_path / "trades.csv"
    equity_csv = tmp_path / "equity.csv"
    result.save_trades(trades_csv)
    result.save_equity(equity_csv)

    lignes = trades_csv.read_text(encoding="utf-8").strip().splitlines()
    assert len(lignes) == len(result.trades) + 1
    assert equity_csv.read_text(encoding="utf-8").startswith("time,balance,equity")


def test_serie_vide():
    result = run_backtest([], BotConfig())
    assert result.report.trades == 0
    assert result.report.final_balance == pytest.approx(10_000.0)


# -------------------------------------------------------------- paper trading


def test_paper_trading_identique_au_backtest(demo_candles):
    """Le paper trading rejoue la même logique : mêmes trades, même solde."""
    cfg = BotConfig()
    reference = run_backtest(demo_candles, cfg)

    trader = PaperTrader(cfg)
    for c in demo_candles:
        trader.on_candle(c)

    # Le backtest ferme la position résiduelle, pas le paper trading
    ouverts = len(trader.broker.positions)
    attendus = reference.trades[: len(reference.trades) - ouverts]

    assert len(trader.broker.trades) == len(attendus)
    for a, b in zip(attendus, trader.broker.trades):
        assert a.open_time == b.open_time
        assert a.pnl == pytest.approx(b.pnl)


def test_paper_trading_via_replay(demo_candles):
    cfg = BotConfig()
    trader = PaperTrader(cfg)
    feed = ReplayFeed(demo_candles, warmup=200)
    trader.warmup(feed.warmup_candles)

    report = trader.run(feed, poll_seconds=0, max_bars=500)
    assert trader.index >= 700
    assert report.trades >= 0


def test_paper_trading_ignore_les_bougies_deja_vues(demo_candles):
    trader = PaperTrader(BotConfig())
    trader.on_candle(demo_candles[0])
    avant = trader.index
    trader.on_candle(demo_candles[0])  # même horodatage
    assert trader.index == avant


def test_etat_sauvegarde_et_recharge(tmp_path, demo_candles):
    state = tmp_path / "state.json"
    cfg = BotConfig()

    trader = PaperTrader(cfg, state_path=state)
    for c in demo_candles[:1200]:
        trader.on_candle(c)
    solde = trader.broker.balance

    assert state.exists()
    repris = PaperTrader(cfg, state_path=state)
    assert repris.broker.balance == pytest.approx(solde)


def test_fichier_d_arret(tmp_path, demo_candles):
    stop = tmp_path / "STOP"
    stop.write_text("stop", encoding="utf-8")

    trader = PaperTrader(BotConfig())
    feed = ReplayFeed(demo_candles)
    trader.run(feed, poll_seconds=0, max_bars=100, stop_file=stop)
    assert trader.index == 0  # arrêt avant toute bougie
