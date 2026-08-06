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
