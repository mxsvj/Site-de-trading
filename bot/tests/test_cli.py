"""Tests de l'interface en ligne de commande."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from smcbot.cli import load_symbol_spec, main
from smcbot.config import xauusd

# Spécification telle que mt5/ExportBars.mq5 l'écrit.
SPEC_MT5 = """{
  "name": "XAUUSD",
  "digits": 2,
  "point": 0.0100000000,
  "contract_size": 100.00,
  "value_per_point_per_lot": 1.000000,
  "min_lot": 0.01,
  "max_lot": 50.00,
  "lot_step": 0.01,
  "spread_points": 28,
  "commission_per_lot": 0.0
}"""


def test_spec_mt5_est_chargeable(tmp_path):
    chemin = tmp_path / "XAUUSD_spec.json"
    chemin.write_text(SPEC_MT5, encoding="utf-8")

    spec = load_symbol_spec(chemin)
    assert spec.name == "XAUUSD"
    assert spec.digits == 2
    assert spec.point == pytest.approx(0.01)
    assert spec.value_per_point_per_lot == pytest.approx(1.0)
    assert spec.spread_points == pytest.approx(28)
    # Cohérent avec le preset écrit à la main
    assert spec.contract_size == xauusd().contract_size


def test_spec_ignore_les_champs_inconnus(tmp_path, capsys):
    """Une version ultérieure du script MQL5 peut ajouter des champs."""
    brut = json.loads(SPEC_MT5)
    brut["swap_long"] = -4.2
    chemin = tmp_path / "spec.json"
    chemin.write_text(json.dumps(brut), encoding="utf-8")

    spec = load_symbol_spec(chemin)
    assert spec.name == "XAUUSD"
    assert "swap_long" in capsys.readouterr().out


def test_spec_illisible(tmp_path):
    chemin = tmp_path / "casse.json"
    chemin.write_text("{ pas du json", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_symbol_spec(chemin)


def test_spec_absente(tmp_path):
    with pytest.raises(SystemExit):
        load_symbol_spec(tmp_path / "inexistant.json")


# ------------------------------------------------------------------ commandes


def test_backtest_demo(capsys):
    assert main(["backtest", "--demo", "--demo-bars", "800"]) == 0
    assert "Résultats" in capsys.readouterr().out


def test_backtest_scalping(capsys):
    code = main(["backtest", "--preset", "xauusd-scalp", "--demo",
                 "--demo-bars", "3000"])
    sortie = capsys.readouterr().out
    assert code == 0
    assert "XAUUSD M1 (biais M15)" in sortie
    assert "Sessions UTC" in sortie


def test_check_renvoie_2_si_defaut(tmp_path, capsys):
    """Le code de sortie doit permettre d'enchaîner dans un script."""
    csv = tmp_path / "double.csv"
    csv.write_text(
        "time,open,high,low,close,volume\n"
        "2024-01-01 00:00:00,2650,2651,2649,2650,10\n"
        "2024-01-01 00:00:00,2650,2651,2649,2650,10\n"
        "2024-01-01 00:01:00,2650,2651,2649,2650,10\n",
        encoding="utf-8",
    )
    assert main(["check", "--csv", str(csv), "--symbol-preset", "xauusd"]) == 2
    assert "double" in capsys.readouterr().out


def test_check_renvoie_0_si_propre(capsys):
    assert main(["check", "--demo", "--demo-bars", "2000"]) == 0
    assert "Aucun défaut détecté" in capsys.readouterr().out


def test_tz_shift_decale_les_trades(tmp_path):
    """Le décalage doit réellement déplacer les horodatages traités."""
    from smcbot.data import save_csv, synthetic_series

    csv = tmp_path / "serie.csv"
    save_csv(
        synthetic_series(4000, start=2650.0, point=0.01, timeframe_minutes=1),
        csv,
    )

    def premieres_dates(shift: str) -> list[str]:
        sortie = tmp_path / f"trades{shift}.csv"
        assert main([
            "backtest", "--csv", str(csv), "--symbol-preset", "xauusd",
            "--timeframe", "M1", "--no-sessions", "--tz-shift", shift,
            "--out-trades", str(sortie),
        ]) == 0
        lignes = sortie.read_text(encoding="utf-8").strip().splitlines()[1:]
        return [ligne.split(",")[0] for ligne in lignes]

    sans = premieres_dates("0")
    avec = premieres_dates("-3")
    assert sans, "aucun trade : le test ne prouverait rien"

    # Les mêmes trades, décalés de trois heures. Le décompte peut différer de
    # quelques unités : déplacer les horodatages déplace aussi les frontières de
    # journée, donc les remises à zéro des compteurs quotidiens.
    attendus = {
        (datetime.strptime(d, "%Y-%m-%d %H:%M") - timedelta(hours=3)) for d in sans
    }
    obtenus = {datetime.strptime(d, "%Y-%m-%d %H:%M") for d in avec}
    communs = attendus & obtenus
    assert len(communs) >= 0.9 * len(attendus), (
        f"seulement {len(communs)}/{len(attendus)} trades retrouvés après décalage"
    )


def test_init_config_et_relecture(tmp_path, capsys):
    chemin = tmp_path / "cfg.json"
    assert main(["init-config", "--preset", "xauusd-scalp", "--out", str(chemin)]) == 0
    assert main(["backtest", "--config", str(chemin), "--demo",
                 "--demo-bars", "2000"]) == 0
    assert "XAUUSD" in capsys.readouterr().out


def test_source_de_donnees_obligatoire():
    with pytest.raises(SystemExit):
        main(["backtest"])


def test_csv_absent_donne_un_message_lisible(tmp_path, capsys):
    """Un export raté ne doit pas se solder par une trace Python."""
    (tmp_path / "autre.csv").write_text("time,open,high,low,close\n", encoding="utf-8")

    with pytest.raises(SystemExit) as sortie:
        main(["check", "--csv", str(tmp_path / "manquant.csv"),
              "--symbol-preset", "xauusd"])

    message = str(sortie.value)
    assert "introuvable" in message
    assert "download" in message          # oriente vers la commande fautive
    assert "autre.csv" in message         # rattrape une faute de frappe


def test_csv_vide(tmp_path):
    csv = tmp_path / "vide.csv"
    csv.write_text("time,open,high,low,close,volume\n", encoding="utf-8")
    with pytest.raises(SystemExit) as sortie:
        main(["check", "--csv", str(csv), "--symbol-preset", "xauusd"])
    assert "Aucune bougie" in str(sortie.value)


def test_optimize(capsys):
    code = main(["optimize", "--demo", "--demo-bars", "1500",
                 "--grid-tp", "2", "--grid-swing", "2,3"])
    assert code == 0
    assert "esp.R" in capsys.readouterr().out


# ------------------------------------------- optimisation avec validation


def test_optimize_separe_apprentissage_et_validation(capsys):
    code = main(["optimize", "--demo", "--demo-bars", "8000",
                 "--grid-tp", "2", "--grid-swing", "2,3", "--split", "0.6"])
    sortie = capsys.readouterr().out

    assert code == 0
    assert "Apprentissage" in sortie and "Validation" in sortie
    assert "apprentissage" in sortie and "validation" in sortie


def test_optimize_refuse_un_split_absurde():
    with pytest.raises(SystemExit):
        main(["optimize", "--demo", "--demo-bars", "8000", "--split", "1.5"])
    with pytest.raises(SystemExit):
        main(["optimize", "--demo", "--demo-bars", "8000", "--split", "0"])


def test_optimize_refuse_un_historique_trop_court():
    with pytest.raises(SystemExit) as sortie:
        main(["optimize", "--demo", "--demo-bars", "600", "--split", "0.6"])
    assert "trop court" in str(sortie.value)


def test_optimize_grid_be(capsys):
    main(["optimize", "--demo", "--demo-bars", "8000", "--grid-tp", "2",
          "--grid-swing", "2", "--grid-be", "0,1", "--split", "0.6"])
    lignes = [l for l in capsys.readouterr().out.splitlines() if "│" in l]
    # deux réglages testés, plus les deux lignes d'en-tête
    assert len(lignes) >= 4


def test_verdict_denonce_le_surapprentissage(capsys):
    """Un réglage bon en apprentissage et mauvais en validation doit être rejeté."""
    from smcbot.cli import _verdict
    from smcbot.metrics import Report

    dedans = Report(expectancy_r=0.25, trades=50)
    dehors = Report(expectancy_r=-0.18, trades=30)
    _verdict([(2.0, 3, 1.0, dedans, dehors)])

    sortie = capsys.readouterr().out
    assert "surapprentissage" in sortie
    assert "Ne l'utilise pas" in sortie


def test_verdict_signale_l_absence_totale_d_avantage(capsys):
    from smcbot.cli import _verdict
    from smcbot.metrics import Report

    _verdict([(2.0, 3, 1.0, Report(expectancy_r=-0.1, trades=40),
               Report(expectancy_r=-0.2, trades=35))])
    sortie = capsys.readouterr().out
    assert "n'a pas d'avantage" in sortie
    assert "bruit" in sortie


def test_verdict_reste_prudent_quand_ca_tient(capsys):
    """Même un résultat qui tient ne doit pas être présenté comme une preuve."""
    from smcbot.cli import _verdict
    from smcbot.metrics import Report

    _verdict([(2.0, 3, 1.0, Report(expectancy_r=0.30, trades=45),
               Report(expectancy_r=0.22, trades=38))])
    sortie = capsys.readouterr().out
    assert "tient en validation" in sortie
    assert "sans être une preuve" in sortie


def test_grid_spread_decompose_le_cout(capsys):
    """Rejouer à spread nul sépare la valeur du signal du coût de transaction."""
    code = main(["optimize", "--demo", "--demo-bars", "8000", "--grid-tp", "2",
                 "--grid-swing", "2", "--grid-spread", "0,24", "--split", "0.6"])
    sortie = capsys.readouterr().out

    assert code == 0
    assert "spread" in sortie                    # la colonne apparaît
    assert "Décomposition" in sortie
    assert "spread nul" in sortie


def test_decomposition_signal_sans_valeur(capsys):
    from smcbot.cli import _decompose_cout
    from smcbot.metrics import Report

    _decompose_cout([
        (2.0, 3, 1.0, 0.0, Report(expectancy_r=-0.08, trades=40), Report(trades=35)),
        (2.0, 3, 1.0, 24.0, Report(expectancy_r=-0.15, trades=42), Report(trades=36)),
    ])
    sortie = capsys.readouterr().out
    assert "ne vaut rien en lui-même" in sortie
    assert "changer de schéma" in sortie


def test_decomposition_signal_mange_par_les_frais(capsys):
    """Un avantage réel mais insuffisant appelle une conclusion opposée."""
    from smcbot.cli import _decompose_cout
    from smcbot.metrics import Report

    _decompose_cout([
        (2.0, 3, 1.0, 0.0,
         Report(expectancy_r=0.12, trades=40),
         Report(expectancy_r=0.12, trades=35)),
        (2.0, 3, 1.0, 24.0,
         Report(expectancy_r=-0.05, trades=42),
         Report(expectancy_r=-0.05, trades=36)),
    ])
    sortie = capsys.readouterr().out
    assert "avantage réel" in sortie
    assert "timeframe supérieur" in sortie
    assert "+0.170" in sortie  # 0.12 - (-0.05)


def test_decomposition_apparie_les_reglages(capsys):
    """Le coût ne se mesure qu'à tp, swing et breakeven identiques."""
    from smcbot.cli import _decompose_cout
    from smcbot.metrics import Report

    _decompose_cout([
        # Réglage A : coût réel de 0,10 R
        (2.0, 3, 1.0, 0.0,
         Report(expectancy_r=0.05, trades=40), Report(expectancy_r=0.05, trades=35)),
        (2.0, 3, 1.0, 24.0,
         Report(expectancy_r=-0.05, trades=40), Report(expectancy_r=-0.05, trades=35)),
        # Réglage B, bien meilleur avec frais : apparier au hasard donnerait
        # un coût négatif absurde.
        (3.0, 4, 0.0, 0.0,
         Report(expectancy_r=0.08, trades=40), Report(expectancy_r=0.08, trades=35)),
        (3.0, 4, 0.0, 24.0,
         Report(expectancy_r=0.02, trades=40), Report(expectancy_r=0.02, trades=35)),
    ])
    sortie = capsys.readouterr().out
    assert "2 réglage(s) apparié(s)" in sortie
    assert "Coût négatif" not in sortie and "impossible" not in sortie
    # Médiane des coûts 0,10 et 0,06
    assert "+0.100" in sortie or "+0.060" in sortie


def test_decomposition_signale_un_cout_negatif(capsys):
    """Un coût négatif révèle des populations de trades différentes."""
    from smcbot.cli import _decompose_cout
    from smcbot.metrics import Report

    _decompose_cout([
        (2.0, 3, 1.0, 0.0,
         Report(expectancy_r=0.09, trades=244), Report(expectancy_r=0.09, trades=35)),
        (2.0, 3, 1.0, 24.0,
         Report(expectancy_r=0.19, trades=91), Report(expectancy_r=0.19, trades=35)),
    ])
    sortie = capsys.readouterr().out
    assert "impossible" in sortie
    assert "pas sur les mêmes trades" in sortie
    # Et surtout aucune conclusion ne doit être tirée
    assert "avantage réel" not in sortie
    assert "survit à ses coûts" not in sortie


def test_decomposition_ignoree_sans_spread_nul(capsys):
    from smcbot.cli import _decompose_cout
    from smcbot.metrics import Report

    _decompose_cout([(2.0, 3, 1.0, 24.0, Report(trades=40), Report(trades=35))])
    assert capsys.readouterr().out == ""


def test_verdict_refuse_de_conclure_sur_trop_peu_de_trades(capsys):
    """Un écart spectaculaire sur dix trades reste du bruit : ne rien affirmer."""
    from smcbot.cli import _verdict
    from smcbot.metrics import Report

    dedans = Report(expectancy_r=1.250, trades=8)
    dehors = Report(expectancy_r=-0.600, trades=5)
    _verdict([(3.0, 3, 1.0, 0.0, dedans, dehors)])

    sortie = capsys.readouterr().out
    assert "Échantillon insuffisant" in sortie
    assert "8 trades" in sortie and "5 en validation" in sortie
    # Surtout : aucun verdict ne doit être prononcé
    assert "surapprentissage" not in sortie
    assert "Ne l'utilise pas" not in sortie
    assert "tient en validation" not in sortie
    # Et la sortie doit orienter vers la cause
    assert "Historique trop court" in sortie


def test_verdict_conclut_des_que_l_echantillon_suffit(capsys):
    from smcbot.cli import MIN_TRADES, _verdict
    from smcbot.metrics import Report

    dedans = Report(expectancy_r=0.25, trades=MIN_TRADES)
    dehors = Report(expectancy_r=-0.18, trades=MIN_TRADES)
    _verdict([(2.0, 3, 1.0, 24.0, dedans, dehors)])
    assert "surapprentissage" in capsys.readouterr().out


def test_decomposition_muette_sur_petit_echantillon(capsys):
    from smcbot.cli import _decompose_cout
    from smcbot.metrics import Report

    _decompose_cout([
        (3.0, 3, 1.0, 0.0, Report(expectancy_r=1.25, trades=8), Report(trades=5)),
        (3.0, 3, 1.0, 24.0, Report(expectancy_r=1.00, trades=9), Report(trades=5)),
    ])
    sortie = capsys.readouterr().out
    assert "Non concluante" in sortie
    assert "avantage réel" not in sortie


def test_scan_exige_metatrader(monkeypatch):
    """Hors Windows, `scan` doit le dire au lieu de planter."""
    import sys
    monkeypatch.setitem(sys.modules, "MetaTrader5", None)
    monkeypatch.delitem(sys.modules, "MetaTrader5")
    with pytest.raises(SystemExit) as sortie:
        main(["scan", "--symbols", "XAUUSD"])
    assert "MetaTrader5" in str(sortie.value)


def test_seuils_de_scalpabilite_sont_ordonnes():
    from smcbot.cli import SCALP_BON, SCALP_LIMITE

    assert 0 < SCALP_BON < SCALP_LIMITE < 1


# ------------------------------------------------- scan sur un MT5 simulé


class _FauxSpec:
    def __init__(self, nom, spread, point=0.01):
        self.name = nom
        self.spread = spread
        self.point = point
        self.digits = 2
        self.trade_contract_size = 100.0
        self.trade_tick_size = point
        self.trade_tick_value = 1.0
        self.volume_min = 0.01
        self.volume_max = 50.0
        self.volume_step = 0.01
        self.swap_long = -4.5
        self.swap_short = 1.2
        self.swap_mode = 1


class _FauxTick:
    def __init__(self, bid, ask):
        self.bid = bid
        self.ask = ask


class _FauxMt5:
    """Terminal MT5 simulé, réduit à ce que `scan` consomme."""

    TIMEFRAME_M5 = 5

    def __init__(self, specs, ticks=None, amplitude=100.0):
        self.specs = specs
        self.ticks = ticks or {}
        self.amplitude = amplitude

    def initialize(self, **_):
        return True

    def shutdown(self):
        return None

    def symbol_select(self, nom, _=True):
        return nom in self.specs

    def symbol_info(self, nom):
        return self.specs.get(nom)

    def symbol_info_tick(self, nom):
        return self.ticks.get(nom)

    def copy_rates_from_pos(self, nom, _tf, _depuis, combien):
        spec = self.specs[nom]
        hauteur = self.amplitude * spec.point
        return [
            {"high": 2000.0 + hauteur, "low": 2000.0, "open": 2000.0, "close": 2000.5}
            for _ in range(combien)
        ]


def _installer_mt5(monkeypatch, faux):
    import sys

    monkeypatch.setitem(sys.modules, "MetaTrader5", faux)


def test_scan_refuse_de_classer_un_spread_nul(monkeypatch, capsys):
    """Un spread à 0 est une mesure absente, pas un coût nul.

    Hors séance, `symbol_info().spread` peut valoir 0. Le classer « favorable »
    ferait passer l'instrument le moins mesurable pour le meilleur.
    """
    faux = _FauxMt5({"GER40": _FauxSpec("GER40", 0), "XAUUSD": _FauxSpec("XAUUSD", 24)})
    _installer_mt5(monkeypatch, faux)

    main(["scan", "--symbols", "GER40,XAUUSD", "--timeframe", "M5", "--bars", "200"])
    sortie = capsys.readouterr().out

    assert "Non classés" in sortie
    assert "favorable" not in sortie.split("Non classés")[1]
    ligne_ger40 = [l for l in sortie.splitlines() if l.startswith("GER40")]
    assert ligne_ger40 == [], "GER40 ne doit pas figurer dans le classement"
    assert any(l.startswith("XAUUSD") for l in sortie.splitlines())


def test_scan_retombe_sur_le_tick_quand_le_champ_spread_est_vide(monkeypatch, capsys):
    """Si le tick courant porte un écart bid/ask, il fait foi."""
    faux = _FauxMt5(
        {"US30": _FauxSpec("US30", 0, point=0.1)},
        ticks={"US30": _FauxTick(bid=40000.0, ask=40001.5)},  # 15 points
        amplitude=300.0,
    )
    _installer_mt5(monkeypatch, faux)

    main(["scan", "--symbols", "US30", "--timeframe", "M5", "--bars", "200"])
    sortie = capsys.readouterr().out

    assert "Non classés" not in sortie
    assert "spread    15 pts" in sortie


def test_scan_avertit_que_le_spread_depend_de_l_heure(monkeypatch, capsys):
    faux = _FauxMt5({"XAUUSD": _FauxSpec("XAUUSD", 24)})
    _installer_mt5(monkeypatch, faux)

    main(["scan", "--symbols", "XAUUSD", "--timeframe", "M5", "--bars", "200"])
    assert "spread de l'instant" in capsys.readouterr().out


def test_spec_depuis_mt5_reproduit_le_format_du_script_mql5(monkeypatch, tmp_path):
    """`--spec-out` doit produire un fichier que `--symbol-spec` relit."""
    from smcbot.data import spec_depuis_mt5

    faux = _FauxMt5({"NAS100": _FauxSpec("NAS100", 10, point=0.1)})
    spec = spec_depuis_mt5(faux, "NAS100")

    chemin = tmp_path / "nas100_spec.json"
    chemin.write_text(json.dumps(spec), encoding="utf-8")
    relu = load_symbol_spec(str(chemin))

    assert relu.name == "NAS100"
    assert relu.point == 0.1
    assert relu.spread_points == 10
    # tick_value 1.0 pour un tick d'une taille égale au point.
    assert relu.value_per_point_per_lot == pytest.approx(1.0)


def test_spec_ignore_les_swaps_exprimes_autrement_qu_en_points():
    """Un swap en devise reporté tel quel serait une erreur d'unité silencieuse."""
    from smcbot.data import spec_depuis_mt5

    spec_devise = _FauxSpec("XAUUSD", 24)
    spec_devise.swap_mode = 0  # points désactivés : montant en devise
    faux = _FauxMt5({"XAUUSD": spec_devise})

    spec = spec_depuis_mt5(faux, "XAUUSD")
    assert spec["swap_long_points"] == 0.0
    assert spec["swap_short_points"] == 0.0


def test_decomposition_ne_conclut_pas_sur_un_avantage_d_apprentissage(capsys):
    """Un avantage brut qui n'existe qu'en apprentissage n'en est pas un.

    Cas réel : +0.071 R sans frais sur la période d'entraînement, -0.041 R sur
    la validation. Conclure « le signal a un avantage réel, passe à un
    timeframe supérieur » enverrait chercher un timeframe pour sauver quelque
    chose qui n'a jamais existé hors de l'échantillon d'entraînement.
    """
    from smcbot.cli import _decompose_cout
    from smcbot.metrics import Report

    _decompose_cout([
        (2.0, 3, 0.0, 0.0,
         Report(expectancy_r=0.071, trades=465),
         Report(expectancy_r=-0.041, trades=197)),
        (2.0, 3, 0.0, 24.0,
         Report(expectancy_r=-0.038, trades=443),
         Report(expectancy_r=-0.041, trades=194)),
    ])
    sortie = capsys.readouterr().out

    assert "avantage réel" not in sortie
    assert "timeframe supérieur" not in sortie
    assert "ne vaut rien en lui-même" in sortie
    assert "n'existe qu'en apprentissage" in sortie
    assert "+0.071" in sortie and "-0.041" in sortie


def test_strategy_param_atteint_la_strategie(capsys):
    """--strategy-param doit arriver jusqu'à la stratégie, pas être ignoré."""
    from smcbot.cli import build_parser, make_config

    args = build_parser().parse_args([
        "backtest", "--demo", "--symbol-preset", "xauusd",
        "--strategy-param", "min_atr_points=200",
        "--strategy-param", "stop_atr=1.5",
    ])
    cfg = make_config(args)
    assert cfg.strategy_params["min_atr_points"] == 200.0
    assert cfg.strategy_params["stop_atr"] == 1.5


def test_strategy_param_mal_forme_est_refuse():
    from smcbot.cli import build_parser, make_config

    args = build_parser().parse_args([
        "backtest", "--demo", "--strategy-param", "min_atr_points",
    ])
    with pytest.raises(SystemExit) as sortie:
        make_config(args)
    assert "NOM=VALEUR" in str(sortie.value)


def test_seuil_fige_survit_a_un_spread_nul():
    """Décomposer la perte exige de garder la même population de trades.

    Sans figeage, rejouer à spread nul ramènerait le seuil de volatilité à
    zéro : toutes les heures calmes entreraient, et on comparerait deux
    stratégies différentes au lieu de mesurer un coût.
    """
    from smcbot.config import BotConfig, xauusd
    from smcbot.scalping import VolBreakStrategy

    cfg = BotConfig(symbol=xauusd())
    cfg.symbol.spread_points = 0.0
    cfg.strategy_params = {"min_atr_points": 200.0}
    assert VolBreakStrategy(cfg).atr_minimal == 200.0

    cfg.strategy_params = {}
    assert VolBreakStrategy(cfg).atr_minimal == 0.0


def test_max_daily_loss_desactivable():
    from smcbot.cli import build_parser, make_config

    args = build_parser().parse_args([
        "backtest", "--demo", "--max-daily-loss", "0",
    ])
    assert make_config(args).risk.max_daily_loss_pct == 0.0


def test_spread_nul_avertit_du_verrou_journalier(capsys):
    """Le piège qui a invalidé deux décompositions doit être signalé.

    Sans frais la stratégie perd moins, déclenche moins le verrou de perte
    journalière, et trade donc bien plus. Comparer les deux runs revient à
    comparer deux populations — avec un écart flatteur.
    """
    from smcbot.cli import _avertir_decomposition, build_parser, make_config

    args = build_parser().parse_args(["backtest", "--demo", "--spread", "0"])
    cfg = make_config(args)
    assert cfg.risk.max_daily_loss_pct > 0
    _avertir_decomposition(args, cfg)
    sortie = capsys.readouterr().out
    assert "--max-daily-loss 0" in sortie
    assert "mêmes trades" in sortie

    # Verrou désactivé : plus rien à signaler.
    args = build_parser().parse_args([
        "backtest", "--demo", "--spread", "0", "--max-daily-loss", "0",
    ])
    _avertir_decomposition(args, make_config(args))
    assert capsys.readouterr().out == ""


def test_flag_strategy_change_bien_de_strategie(capsys):
    from smcbot.cli import build_parser, make_config

    args = build_parser().parse_args([
        "backtest", "--demo", "--strategy", "vol-break",
    ])
    assert make_config(args).strategy == "vol-break"


def test_backtest_affiche_les_motifs_de_refus(capsys):
    """Le tableau de bord des filtres doit sortir : c'est l'outil de diagnostic."""
    code = main([
        "backtest", "--demo", "--demo-bars", "8000", "--symbol-preset", "xauusd",
        "--strategy", "vol-break", "--no-sessions",
        "--strategy-param", "min_atr_points=60",
    ])
    assert code == 0
    assert "Résultats" in capsys.readouterr().out
