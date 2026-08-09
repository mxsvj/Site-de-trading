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


# `lead-lag` lit une seconde série : le registre ne peut pas l'instancier sans.
def _params_pour(nom: str, tmp_path) -> dict:
    if nom != "lead-lag":
        return {}
    from smcbot.data import save_csv, synthetic_series as serie

    chemin = tmp_path / "reference.csv"
    save_csv(serie(6000, start=1.10, point=0.00001, timeframe_minutes=1), chemin)
    return {"reference_csv": str(chemin)}


def test_toutes_les_strategies_s_instancient(tmp_path):
    for nom in STRATEGIES:
        cfg = BotConfig(strategy=nom, symbol=xauusd())
        cfg.strategy_params = _params_pour(nom, tmp_path)
        strategie = make_strategy(cfg)
        assert strategie.name == nom


def test_lead_lag_refuse_de_demarrer_sans_reference():
    """Sans série de référence, la stratégie n'a aucune information.

    Retourner silencieusement zéro signal ferait passer une configuration
    incomplète pour une absence de setups.
    """
    cfg = BotConfig(strategy="lead-lag", symbol=xauusd())
    with pytest.raises(ValueError) as erreur:
        make_strategy(cfg)
    assert "reference_csv" in str(erreur.value)


def test_strategie_inconnue_est_refusee():
    with pytest.raises(ValueError) as erreur:
        make_strategy(BotConfig(strategy="martingale"))
    assert "Disponibles" in str(erreur.value)


@pytest.mark.parametrize("nom", sorted(STRATEGIES))
def test_chaque_strategie_tourne_de_bout_en_bout(nom, tmp_path):
    """Aucune ne doit planter, ni produire un signal incohérent."""
    cfg = BotConfig(strategy=nom, symbol=xauusd(), timeframe="M1")
    cfg.strategy_params = _params_pour(nom, tmp_path)
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


def _lot(n: int, decalage: int = 0) -> list[Essai]:
    """n hypothèses distinctes, identifiables par leur résultat."""
    from smcbot.metrics import Report

    return [
        Essai(
            strategy="smc",
            label=f"smc #{i + decalage}",
            params={"tp_r": float(i + decalage)},
            dedans=Report(trades=100 + i + decalage, expectancy_r=0.1),
            dehors=Report(trades=50 + i + decalage, expectancy_r=0.1),
        )
        for i in range(n)
    ]


def test_journal_cumule_entre_sessions(tmp_path):
    """Tester en dix fois ne coûte pas moins cher qu'en une."""
    chemin = tmp_path / "hypotheses.json"

    premier = Journal(chemin)
    premier.enregistrer(_lot(12), "session 1")
    assert premier.total == 12

    second = Journal(chemin)
    assert second.total == 12, "le compteur doit survivre à la session"
    second.enregistrer(_lot(8, decalage=100), "session 2")
    assert Journal(chemin).total == 20


def test_journal_sans_fichier_reste_en_memoire():
    journal = Journal(None)
    journal.enregistrer(_lot(5), "essai")
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
    verdict = juger([essai(0.2, 0.2, 2.5)], journal)
    assert verdict.significatif

    beaucoup = Journal(None)
    beaucoup.herite = 200
    verdict = juger([essai(0.2, 0.2, 2.5)], beaucoup)
    assert not verdict.significatif
    assert "relever la barre" in verdict.to_text()


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


def test_telechargement_par_tranches(monkeypatch):
    """Une grosse demande est découpée, et les tranches sont recollées sans doublon."""
    import types
    from datetime import timezone as tz
    from smcbot.data import _copier_par_tranches

    base = int(datetime(2024, 6, 1, tzinfo=tz.utc).timestamp())

    class FauxTerminal:
        """Sert au plus 20 000 bougies par appel, et n'en possède que 45 000."""

        DISPONIBLES = 45_000

        def __init__(self):
            self.appels = 0

        def copy_rates_from(self, symbol, tf, ancre, count):
            self.appels += 1
            fin = int(ancre.timestamp())
            debut_possible = base - self.DISPONIBLES * 60
            rates = []
            for i in range(count):
                t = fin - i * 60
                if t < debut_possible:
                    break
                rates.append({"time": t, "open": 1.0, "high": 1.0,
                              "low": 1.0, "close": 1.0, "tick_volume": 1})
            return list(reversed(rates))

    faux = FauxTerminal()
    monkeypatch.setattr(
        "smcbot.data.datetime",
        types.SimpleNamespace(
            now=lambda tzinfo=None: datetime.fromtimestamp(base, tz=tz.utc),
            fromtimestamp=datetime.fromtimestamp,
        ),
    )

    rates = _copier_par_tranches(faux, "XAUUSD", 1, 100_000)

    assert faux.appels > 1, "une demande de 100 000 doit être découpée"
    horodatages = [int(r["time"]) for r in rates]
    assert horodatages == sorted(horodatages), "les bougies doivent être ordonnées"
    assert len(set(horodatages)) == len(horodatages), "aucun doublon entre tranches"
    # Le terminal n'en a que 45 000 : la boucle doit s'arrêter là, pas tourner
    # jusqu'aux 100 000 demandées.
    assert FauxTerminal.DISPONIBLES <= len(rates) < FauxTerminal.DISPONIBLES + 10


def test_trades_necessaires_suit_la_racine():
    """Quadrupler l'échantillon double le t : la projection doit le refléter."""
    from smcbot.lab import trades_necessaires

    observe = essai(0.5, 0.5, 1.5, trades=17)
    besoin = trades_necessaires(observe, seuil=3.0)
    # t = 1.5 sur 17 trades ; pour t = 3.0 il faut 4 fois plus de trades
    assert besoin == pytest.approx(17 * 4, rel=0.15)

    assert trades_necessaires(essai(0.1, -0.1, -0.5), 3.0) is None
    assert trades_necessaires(essai(0.1, 0.1, 1.0, trades=0), 3.0) is None


def test_verdict_dit_combien_de_donnees_manquent():
    """« Échantillon insuffisant » sans suite ne dit pas quoi faire."""
    petit = essai(0.9, 0.6, 1.57, trades=17)
    petit.label = "asian-sweep tp=2"
    texte = juger([petit], Journal(None)).to_text()

    assert "Rien à conclure" in texte
    assert "asian-sweep tp=2" in texte
    assert "fois plus de données" in texte


def test_verdict_projette_aussi_sur_un_echantillon_valide():
    """Échantillon suffisant mais t insuffisant : dire ce qu'il manque."""
    proche = essai(0.3, 0.25, 1.4, trades=40)
    texte = juger([proche], Journal(None)).to_text()
    assert "il faudrait environ" in texte
    assert "racine du nombre de trades" in texte


def test_aucune_projection_si_la_validation_perd():
    perdant = essai(0.5, -0.2, -1.2, trades=10)
    texte = juger([perdant], Journal(None)).to_text()
    assert "fois plus de données" not in texte


# ----------------------------------------------- exécution au marché vs limite


def test_ordre_au_marche_est_rempli_a_la_cloture():
    """Le lookahead corrigé : décider à la clôture, c'est entrer à la clôture."""
    from smcbot.broker import PaperBroker
    from smcbot.config import BotConfig, SymbolSpec
    from smcbot.smc import BULLISH
    from smcbot.strategy import Signal

    cfg = BotConfig()
    cfg.symbol = SymbolSpec(point=0.01, value_per_point_per_lot=1.0,
                            spread_points=0.0, lot_step=0.01)
    cfg.risk.initial_balance = 100_000
    cfg.filters.weekdays = []
    broker = PaperBroker(cfg)

    # Bougie de balayage haussière : ouverture BASSE, clôture haute.
    bougie = Candle(T0, 2640.0, 2661.0, 2630.0, 2660.0)
    broker.on_candle(bougie, 0)

    signal = Signal(index=0, time=T0, direction=BULLISH, entry_level=2660.0,
                    stop=2629.0, tp_r=3.0, reason="balayage",
                    entry_type="market")
    pos = broker.execute(signal, bougie, 0)

    assert pos is not None
    assert pos.entry == pytest.approx(2660.0), (
        "un ordre au marché doit être rempli à la clôture, pas à l'ouverture"
    )


def test_ordre_limite_profite_encore_de_l_ouverture():
    """Un ordre posé à l'avance garde ce droit : la correction ne le touche pas."""
    from smcbot.broker import PaperBroker
    from smcbot.config import BotConfig, SymbolSpec
    from smcbot.smc import BULLISH
    from smcbot.strategy import Signal

    cfg = BotConfig()
    cfg.symbol = SymbolSpec(point=0.01, value_per_point_per_lot=1.0,
                            spread_points=0.0, lot_step=0.01)
    cfg.risk.initial_balance = 100_000
    cfg.filters.weekdays = []
    broker = PaperBroker(cfg)

    bougie = Candle(T0, 2640.0, 2661.0, 2630.0, 2660.0)
    broker.on_candle(bougie, 0)

    # Limite posée à 2650 : la bougie ouvre à 2640, sous la limite → on est
    # rempli à l'ouverture, ce qui est légitime pour un ordre déjà en carnet.
    signal = Signal(index=0, time=T0, direction=BULLISH, entry_level=2650.0,
                    stop=2629.0, tp_r=3.0, reason="order block")
    pos = broker.execute(signal, bougie, 0)

    assert pos is not None
    assert pos.entry == pytest.approx(2640.0)


def test_toutes_les_strategies_de_scalping_entrent_au_marche():
    """Une stratégie qui décide à la clôture ne doit jamais poser d'ordre limite."""
    from smcbot.config import xauusd

    candles = synthetic_series(9000, start=2650.0, point=0.01, timeframe_minutes=1)
    for nom in ("asian-sweep", "orb", "fade"):
        cfg = BotConfig(strategy=nom, symbol=xauusd(), timeframe="M1")
        strategie = make_strategy(cfg)
        signaux = [s for s in (strategie.on_candle(c) for c in candles) if s]
        assert signaux, f"{nom} n'a produit aucun signal"
        assert all(s.entry_type == "market" for s in signaux), nom


def test_essai_distingue_le_silence_du_baillon():
    """Peu de trades par manque de signal, ou par refus : ce n'est pas pareil.

    Un candidat qui déclenche 300 fois et se voit refuser 296 entrées n'a pas
    été évalué. Le confondre avec un schéma qui ne se présente jamais ferait
    conclure sur une stratégie jamais testée.
    """
    from smcbot.lab import Essai
    from smcbot.metrics import Report

    muette = Essai(strategy="orb", label="orb", dedans=Report(trades=4), refus={})
    assert not muette.etouffe
    assert muette.motif_dominant is None

    baillonnee = Essai(
        strategy="orb",
        label="orb",
        dedans=Report(trades=4),
        refus={"volume sous le lot minimal (risque trop faible)": 296, "session": 3},
    )
    assert baillonnee.etouffe
    assert baillonnee.refuses == 299
    motif, combien = baillonnee.motif_dominant
    assert combien == 296
    assert "lot minimal" in motif


def test_essai_avec_beaucoup_de_trades_n_est_pas_etouffe():
    from smcbot.lab import Essai
    from smcbot.metrics import Report

    essai = Essai(
        strategy="smc",
        label="smc",
        dedans=Report(trades=879),
        refus={"frais trop lourds": 40},
    )
    assert not essai.etouffe


def test_relancer_la_meme_commande_ne_consomme_pas_d_hypothese(tmp_path):
    """Bonferroni corrige le nombre d'hypothèses examinées, pas d'exécutions.

    Relancer une commande identique n'explore aucune possibilité nouvelle. La
    compter relèverait le seuil sans qu'aucune recherche supplémentaire ait eu
    lieu — pénaliser l'utilisateur pour avoir appuyé deux fois sur Entrée.
    """
    from smcbot.lab import Essai, Journal
    from smcbot.metrics import Report

    def lot():
        return [
            Essai(
                strategy="smc",
                label="smc tp=2",
                params={"tp_r": 2.0},
                dedans=Report(trades=443, expectancy_r=-0.038),
                dehors=Report(trades=194, expectancy_r=-0.041),
            )
        ]

    chemin = tmp_path / "h.json"
    assert Journal(chemin).enregistrer(lot(), "smc") == 1
    assert Journal(chemin).total == 1

    journal = Journal(chemin)
    assert journal.enregistrer(lot(), "smc") == 0
    assert journal.rejouees == 1
    assert journal.total == 1, "le seuil ne doit pas monter pour une relance"


def test_deux_reglages_au_resultat_identique_comptent_pour_un(tmp_path):
    """Un filtre sans effet ne crée pas une hypothèse de plus."""
    from smcbot.lab import Essai, Journal
    from smcbot.metrics import Report

    def essai(label):
        return Essai(
            strategy="asian-sweep",
            label=label,
            params={"tp_r": 2.0},
            dedans=Report(trades=118, expectancy_r=-0.237),
            dehors=Report(trades=46, expectancy_r=-0.283),
        )

    journal = Journal(tmp_path / "h.json")
    assert journal.enregistrer([essai("sans filtre"), essai("avec filtre")], "x") == 1
    assert journal.total == 1


def test_journal_ancien_format_conserve_son_total(tmp_path):
    """Un compteur écrit avant le suivi par empreinte n'est pas effacé."""
    import json

    from smcbot.lab import Journal

    chemin = tmp_path / "h.json"
    chemin.write_text(json.dumps({"total": 34, "sessions": []}), encoding="utf-8")

    journal = Journal(chemin)
    assert journal.total == 34


def test_un_parametre_neutre_ne_cree_pas_une_hypothese(tmp_path):
    """Ajouter une option désactivée ne rejoue pas la recherche.

    `smc tp=2` et `smc tp=2 temps=0` sont le même essai : la sortie sur le
    temps est désactivée. Compter le second relèverait le seuil sans qu'aucune
    possibilité nouvelle ait été explorée.
    """
    from smcbot.lab import Essai, Journal
    from smcbot.metrics import Report

    def essai(params, label):
        return Essai(
            strategy="smc",
            label=label,
            params=params,
            dedans=Report(trades=443, expectancy_r=-0.038),
            dehors=Report(trades=194, expectancy_r=-0.041),
        )

    journal = Journal(tmp_path / "h.json")
    journal.enregistrer([essai({"tp_r": 2.0}, "smc tp=2")], "avant")
    assert journal.total == 1

    suite = Journal(tmp_path / "h.json")
    nouvelles = suite.enregistrer(
        [essai({"tp_r": 2.0, "max_bars_in_trade": 0}, "smc tp=2 temps=off")], "après"
    )
    assert nouvelles == 0
    assert suite.total == 1


def test_la_grille_ne_efface_pas_les_parametres_de_configuration():
    """Les réglages de base doivent survivre à la grille du banc d'essai.

    `evaluer` remplaçait `strategy_params` par les seuls paramètres de la
    grille. Tout ce qui venait de --strategy-param disparaissait en silence :
    une série de référence, un seuil de volatilité figé. La stratégie tournait
    alors avec d'autres réglages que ceux demandés, sans rien signaler.
    """
    from smcbot.data import synthetic_series as serie
    from smcbot.lab import evaluer

    base = BotConfig(symbol=xauusd(), timeframe="M1")
    base.strategy_params = {"min_atr_points": 200.0, "max_cost": 0.12}
    base.filters.sessions = []
    base.filters.weekdays = []

    vus = []
    candles = serie(6000, start=2650.0, point=0.01, timeframe_minutes=1)

    import smcbot.lab as lab

    reel = lab.run_backtest

    def espion(bougies, cfg=None, warmup=0):
        vus.append(dict(cfg.strategy_params))
        return reel(bougies, cfg, warmup)

    lab.run_backtest = espion
    try:
        evaluer([("vol-break", {"tp_r": 3.0}, "vol-break tp=3")], candles, base)
    finally:
        lab.run_backtest = reel

    assert vus, "aucun backtest lancé"
    for params in vus:
        assert params["min_atr_points"] == 200.0, "paramètre de base effacé"
        assert params["tp_r"] == 3.0, "paramètre de grille non appliqué"
