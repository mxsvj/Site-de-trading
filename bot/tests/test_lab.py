"""Tests du banc d'essai et des stratégies de scalping."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from smcbot.backtest import run_backtest
from smcbot.config import BotConfig, xauusd
from smcbot.data import Candle, synthetic_series
from smcbot.lab import (
    ALPHA,
    MIN_TRADES,
    Essai,
    Journal,
    evaluer,
    juger,
    seuil_bonferroni,
)
from smcbot.metrics import Report
from smcbot.registry import STRATEGIES, make_strategy

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def m1(minute: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(T0 + timedelta(minutes=minute), o, h, l, c, 1.0)


# ------------------------------------------------------------------ registre


def test_toutes_les_strategies_s_instancient():
    for nom in STRATEGIES:
        cfg = BotConfig(strategy=nom, symbol=xauusd())
        strategie = make_strategy(cfg)
        assert strategie.name == nom


def test_strategie_inconnue_est_refusee():
    with pytest.raises(ValueError) as erreur:
        make_strategy(BotConfig(strategy="martingale"))
    assert "Disponibles" in str(erreur.value)


@pytest.mark.parametrize("nom", sorted(STRATEGIES))
def test_chaque_strategie_tourne_de_bout_en_bout(nom):
    """Aucune ne doit planter, ni produire un signal incohérent."""
    cfg = BotConfig(strategy=nom, symbol=xauusd(), timeframe="M1")
    cfg.filters.sessions = []
    cfg.filters.weekdays = []
    candles = synthetic_series(6000, start=2650.0, point=0.01, timeframe_minutes=1)

    result = run_backtest(candles, cfg)
    for trade in result.trades:
        assert trade.lots > 0
        assert trade.stop != trade.entry
        if trade.direction == "bullish":
            assert trade.stop < trade.entry
        else:
            assert trade.stop > trade.entry


def test_config_json_conserve_la_strategie(tmp_path):
    chemin = tmp_path / "cfg.json"
    origine = BotConfig(strategy="asian-sweep", strategy_params={"tp_r": 1.5})
    origine.to_json(chemin)

    relu = BotConfig.from_json(chemin)
    assert relu.strategy == "asian-sweep"
    assert relu.strategy_params["tp_r"] == 1.5


# --------------------------------------------------------------- stratégies


def test_asian_sweep_detecte_le_balayage():
    """Mèche sous la plage asiatique puis clôture au-dessus : signal d'achat."""
    cfg = BotConfig(strategy="asian-sweep", symbol=xauusd())
    cfg.strategy_params = {"asian_start": "00:00", "asian_end": "06:00",
                           "hunt_start": "07:00", "hunt_end": "11:00"}
    strategie = make_strategy(cfg)

    # Séance asiatique : plage 2640 → 2660
    for i in range(0, 360, 30):
        assert strategie.on_candle(m1(i, 2650, 2660, 2640, 2650)) is None

    # 07:30 : balayage du bas, réintégration
    signal = strategie.on_candle(m1(450, 2645, 2648, 2635, 2647))
    assert signal is not None
    assert signal.direction == "bullish"
    assert signal.stop < 2635
    assert "balayage" in signal.reason

    # Un seul signal par sens et par jour
    assert strategie.on_candle(m1(460, 2645, 2648, 2630, 2647)) is None


def test_asian_sweep_ignore_hors_fenetre():
    cfg = BotConfig(strategy="asian-sweep", symbol=xauusd())
    strategie = make_strategy(cfg)
    for i in range(0, 360, 30):
        strategie.on_candle(m1(i, 2650, 2660, 2640, 2650))
    # 06:30 : hors de la fenêtre de chasse (07:00-11:00)
    assert strategie.on_candle(m1(390, 2645, 2648, 2635, 2647)) is None


def test_orb_casse_la_plage_d_ouverture():
    cfg = BotConfig(strategy="orb", symbol=xauusd())
    cfg.strategy_params = {"session_start": "07:00", "range_minutes": 60,
                           "buffer_points": 10}
    strategie = make_strategy(cfg)

    # 07:00 → 08:00 : plage 2640 → 2660
    for i in range(420, 480, 10):
        assert strategie.on_candle(m1(i, 2650, 2660, 2640, 2650)) is None

    signal = strategie.on_candle(m1(485, 2658, 2666, 2657, 2665))
    assert signal is not None
    assert signal.direction == "bullish"
    assert signal.stop == pytest.approx(2640)
    assert strategie.on_candle(m1(490, 2665, 2670, 2664, 2669)) is None


def test_fade_prend_le_contrepied():
    cfg = BotConfig(strategy="fade", symbol=xauusd())
    cfg.strategy_params = {"ema_period": 5, "atr_period": 5, "atr_mult": 2.0}
    strategie = make_strategy(cfg)

    for i in range(30):
        strategie.on_candle(m1(i, 2650, 2652, 2648, 2650))

    # Extension brutale vers le haut : la stratégie vend
    signal = strategie.on_candle(m1(31, 2650, 2700, 2650, 2699))
    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.stop > signal.entry_level


# -------------------------------------------------------------- statistique


def test_seuil_monte_avec_le_nombre_d_hypotheses():
    """Chercher davantage oblige à exiger davantage."""
    assert seuil_bonferroni(1) == pytest.approx(1.96, abs=0.01)
    assert seuil_bonferroni(100) == pytest.approx(3.48, abs=0.02)
    assert seuil_bonferroni(1000) > seuil_bonferroni(100)
    assert seuil_bonferroni(0) == seuil_bonferroni(1)


def test_journal_cumule_entre_sessions(tmp_path):
    """Tester en dix fois ne coûte pas moins cher qu'en une."""
    chemin = tmp_path / "hypotheses.json"

    premier = Journal(chemin)
    premier.enregistrer(12, "session 1")
    assert premier.total == 12

    second = Journal(chemin)
    assert second.total == 12, "le compteur doit survivre à la session"
    second.enregistrer(8, "session 2")
    assert Journal(chemin).total == 20


def test_journal_sans_fichier_reste_en_memoire():
    journal = Journal(None)
    journal.enregistrer(5, "essai")
    assert journal.total == 5


def test_journal_illisible_repart_a_zero(tmp_path):
    chemin = tmp_path / "casse.json"
    chemin.write_text("{ pas du json", encoding="utf-8")
    assert Journal(chemin).total == 0


def essai(dedans_r: float, dehors_r: float, t: float, trades: int = 60) -> Essai:
    return Essai(
        strategy="test",
        label="test",
        dedans=Report(expectancy_r=dedans_r, trades=trades),
        dehors=Report(expectancy_r=dehors_r, trades=trades, sharpe=t),
    )


def test_verdict_exige_plus_quand_on_a_plus_cherche(capsys):
    """Le même t peut être significatif seul et ne plus l'être après 100 essais."""
    journal = Journal(None)
    journal.total = 0
    verdict = juger([essai(0.2, 0.2, 2.5)], journal)
    assert verdict.significatif

    beaucoup = Journal(None)
    beaucoup.total = 200
    verdict = juger([essai(0.2, 0.2, 2.5)], beaucoup)
    assert not verdict.significatif
    assert "relèvera encore la barre" in verdict.to_text()


def test_verdict_rejette_une_validation_perdante():
    verdict = juger([essai(0.5, -0.1, 3.0)], Journal(None))
    assert not verdict.significatif
    assert "perd hors échantillon" in verdict.to_text()


def test_verdict_ignore_les_echantillons_trop_petits():
    petit = essai(2.0, 2.0, 9.0, trades=MIN_TRADES - 1)
    verdict = juger([petit], Journal(None))
    assert verdict.meilleur is None
    assert verdict.exploitables == 0
    assert "Rien à conclure" in verdict.to_text()


def test_sharpe_neutralise_une_dispersion_nulle():
    """Des trades tous identiques donnaient un t astronomique."""
    from smcbot.metrics import _sharpe

    assert _sharpe([1.5] * 6) == 0.0
    assert _sharpe([1.5, 1.5, 1.5 + 1e-15]) == 0.0
    assert _sharpe([2.0, -1.0, 2.0, -1.0]) != 0.0


def test_evaluer_separe_les_periodes():
    candles = synthetic_series(8000, start=2650.0, point=0.01, timeframe_minutes=1)
    cfg = BotConfig(symbol=xauusd(), timeframe="M1")
    cfg.filters.sessions = []
    cfg.filters.weekdays = []

    essais = evaluer(
        [("fade", {"tp_r": 2.0}, "fade"), ("orb", {"tp_r": 2.0}, "orb")],
        candles, cfg, split=0.6, prechauffe=500,
    )
    assert len(essais) == 2
    assert all(e.strategy in ("fade", "orb") for e in essais)


def test_tp_r_est_applique_a_toutes_les_strategies():
    """Un tp_r qui n'atteint pas la stratégie fait tester trois fois la même."""
    candles = synthetic_series(9000, start=2650.0, point=0.01, timeframe_minutes=1)
    cfg = BotConfig(symbol=xauusd(), timeframe="M1")
    cfg.filters.sessions = []
    cfg.filters.weekdays = []

    essais = evaluer(
        [("smc", {"tp_r": tp}, f"smc tp={tp}") for tp in (1.5, 3.0)],
        candles, cfg, split=0.6, prechauffe=500,
    )
    resultats = {(e.dedans.trades, round(e.dedans.expectancy_r, 6)) for e in essais}
    assert len(resultats) > 1, "le take profit n'a eu aucun effet sur la stratégie"


def test_doublons_sont_signales():
    from smcbot.lab import doublons

    a = essai(0.1, 0.1, 1.0)
    a.label = "smc tp=1.5"
    b = essai(0.1, 0.1, 1.0)
    b.label = "smc tp=3"
    c = essai(0.2, 0.2, 1.0)
    c.label = "fade tp=2"
    c.strategy = "fade"

    groupes = doublons([a, b, c])
    assert len(groupes) == 1
    assert {e.label for e in groupes[0]} == {"smc tp=1.5", "smc tp=3"}


def test_verdict_signale_les_hypotheses_dupliquees():
    a = essai(0.1, 0.1, 1.0)
    a.label = "smc tp=1.5"
    b = essai(0.1, 0.1, 1.0)
    b.label = "smc tp=3"

    texte = juger([a, b], Journal(None)).to_text()
    assert "Résultats identiques" in texte
    assert "aucun effet" in texte
