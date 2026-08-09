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
        (2.0, 3, 1.0, 0.0, Report(expectancy_r=0.12, trades=40), Report(trades=35)),
        (2.0, 3, 1.0, 24.0, Report(expectancy_r=-0.05, trades=42), Report(trades=36)),
    ])
    sortie = capsys.readouterr().out
    assert "avantage réel" in sortie
    assert "timeframe supérieur" in sortie
    assert "0.170" in sortie  # 0.12 - (-0.05)


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
    ])
    sortie = capsys.readouterr().out
    assert "Non concluante" in sortie
    assert "avantage réel" not in sortie
