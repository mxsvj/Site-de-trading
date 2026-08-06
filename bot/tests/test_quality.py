"""Tests du contrôle de qualité des données."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from smcbot.config import xauusd
from smcbot.data import Candle, synthetic_series
from smcbot.quality import inspect_series, shift_times

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)  # un lundi


def serie_m1(n: int, depart: datetime = T0) -> list[Candle]:
    """Série M1 propre, à prix d'or, sans trou."""
    return [
        Candle(
            depart + timedelta(minutes=i),
            2650.0 + i * 0.01,
            2650.5 + i * 0.01,
            2649.5 + i * 0.01,
            2650.2 + i * 0.01,
        )
        for i in range(n)
    ]


def test_serie_propre_ne_declenche_rien():
    report = inspect_series(serie_m1(500), xauusd())
    assert report.bars == 500
    assert report.inferred_minutes == 1
    assert report.duplicates == 0
    assert report.out_of_order == 0
    assert report.invalid_ohlc == 0
    assert report.gaps == []


def test_serie_vide():
    report = inspect_series([], xauusd())
    assert report.bars == 0
    assert not report.clean


def test_detection_des_doublons():
    bougies = serie_m1(50)
    bougies.insert(10, bougies[10])  # horodatage répété
    report = inspect_series(bougies, xauusd())

    assert report.duplicates == 1
    assert any("double" in w for w in report.warnings)


def test_detection_hors_sequence():
    bougies = serie_m1(50)
    bougies[20], bougies[21] = bougies[21], bougies[20]
    report = inspect_series(bougies, xauusd())

    assert report.out_of_order >= 1
    assert any("hors séquence" in w for w in report.warnings)


def test_detection_des_trous():
    bougies = serie_m1(60)
    del bougies[20:35]  # 15 minutes manquantes en pleine session
    report = inspect_series(bougies, xauusd())

    assert report.gaps
    assert report.gaps[0][1] == 15


def test_coupure_de_week_end_n_est_pas_un_trou():
    """Le saut du vendredi soir au dimanche soir est normal."""
    vendredi = datetime(2024, 1, 5, 20, 0, tzinfo=timezone.utc)
    bougies = serie_m1(30, vendredi)
    bougies += serie_m1(30, datetime(2024, 1, 7, 22, 0, tzinfo=timezone.utc))

    report = inspect_series(bougies, xauusd())
    assert report.gaps == []


def test_ohlc_incoherent():
    bougies = serie_m1(20)
    bougies[5] = Candle(bougies[5].time, 2650.0, 2649.0, 2651.0, 2650.0)  # high < low
    report = inspect_series(bougies, xauusd())

    assert report.invalid_ohlc == 1
    assert any("OHLC" in w for w in report.warnings)


def test_bougies_du_samedi_signalees():
    samedi = datetime(2024, 1, 6, 10, 0, tzinfo=timezone.utc)
    report = inspect_series(serie_m1(60, samedi), xauusd())

    assert report.saturday_bars == 60
    assert any("samedi" in w for w in report.warnings)


def test_decimales_incoherentes():
    """Des prix à 5 décimales sur un symbole qui en déclare 2."""
    bougies = [
        Candle(T0 + timedelta(minutes=i), 2650.12345, 2650.5, 2649.5, 2650.23456)
        for i in range(30)
    ]
    report = inspect_series(bougies, xauusd())
    assert report.decimals_seen == 5
    assert any("décimales" in w for w in report.warnings)


def test_amplitude_mediane():
    """L'amplitude sert à juger si le plancher de stop est réaliste."""
    bougies = [
        Candle(T0 + timedelta(minutes=i), 2650.0, 2651.0, 2650.0, 2650.5)
        for i in range(50)
    ]
    report = inspect_series(bougies, xauusd())
    assert report.median_range_points == pytest.approx(100.0)  # 1,00 $ = 100 points


# ------------------------------------------------------------- fuseau horaire


def serie_avec_pause(quiet_hour: int, jours: int = 5) -> list[Candle]:
    """Série horaire dont l'heure `quiet_hour` est presque vide (pause du marché)."""
    bougies: list[Candle] = []
    for jour in range(jours):
        base = T0 + timedelta(days=jour)
        for heure in range(24):
            combien = 1 if heure == quiet_hour else 10
            for minute in range(combien):
                instant = base + timedelta(hours=heure, minutes=minute)
                bougies.append(Candle(instant, 2650.0, 2650.5, 2649.5, 2650.2))
    return bougies


def test_serie_en_utc_ne_suggere_aucun_decalage():
    report = inspect_series(serie_avec_pause(21), xauusd())
    assert report.quiet_hour == 21
    assert report.suggested_tz_shift == 0
    assert not any("UTC" in w for w in report.warnings)


def test_detection_d_un_serveur_utc_plus_3():
    """La pause quotidienne apparaît à minuit sur un serveur UTC+3."""
    report = inspect_series(serie_avec_pause(0), xauusd())
    assert report.quiet_hour == 0
    assert report.suggested_tz_shift == 3
    assert any("--tz-shift -3" in w for w in report.warnings)


def test_detection_avec_heure_totalement_vide():
    """Cas réel : la pause quotidienne ne contient aucune bougie du tout.

    Une heure absente ne crée pas de clé dans un Counter ; le diagnostic doit
    donc raisonner sur les 24 heures, pas seulement sur celles observées.
    """
    bougies: list[Candle] = []
    for jour in range(5):
        base = T0 + timedelta(days=jour)
        for heure in range(24):
            if heure == 0:
                continue  # marché fermé, pas une seule bougie
            for minute in range(10):
                bougies.append(
                    Candle(
                        base + timedelta(hours=heure, minutes=minute),
                        2650.0, 2650.5, 2649.5, 2650.2,
                    )
                )

    report = inspect_series(bougies, xauusd())
    assert report.quiet_hour == 0
    assert report.suggested_tz_shift == 3
    assert any("--tz-shift -3" in w for w in report.warnings)


def test_decalage_negatif():
    """Une pause observée à 18h correspond à un serveur UTC-3."""
    report = inspect_series(serie_avec_pause(18), xauusd())
    assert report.suggested_tz_shift == -3


def test_pas_de_diagnostic_horaire_sur_serie_trop_courte():
    report = inspect_series(serie_m1(100), xauusd())
    assert report.quiet_hour == -1
    assert report.suggested_tz_shift == 0


def test_distribution_plate_ne_suggere_aucun_decalage():
    """Un flux continu 24 h/24 n'a pas de pause : ne rien inventer.

    Prendre le minimum d'une distribution plate reviendrait à tirer un décalage
    horaire au hasard, et à envoyer l'utilisateur corriger un défaut inexistant.
    """
    bougies: list[Candle] = []
    for jour in range(5):
        base = T0 + timedelta(days=jour)
        for heure in range(24):
            for minute in range(10):
                bougies.append(
                    Candle(
                        base + timedelta(hours=heure, minutes=minute),
                        2650.0, 2650.5, 2649.5, 2650.2,
                    )
                )
    # Une seule bougie en moins suffirait à créer un minimum trompeur
    del bougies[47]

    report = inspect_series(bougies, xauusd())
    assert report.quiet_hour == -1
    assert report.suggested_tz_shift == 0
    assert not any("UTC" in w for w in report.warnings)


def test_shift_times_corrige_la_serie():
    bougies = serie_avec_pause(0)
    corrigees = shift_times(bougies, -3)

    assert corrigees[0].time == bougies[0].time - timedelta(hours=3)
    assert corrigees[0].open == bougies[0].open  # les prix ne bougent pas

    report = inspect_series(corrigees, xauusd())
    assert report.suggested_tz_shift == 0


def test_shift_times_est_reversible():
    bougies = serie_m1(20)
    assert shift_times(shift_times(bougies, 3), -3)[0].time == bougies[0].time


def test_serie_synthetique_est_propre():
    """La série de démonstration ne doit pas déclencher de faux positifs."""
    candles = synthetic_series(3000, start=2650.0, point=0.01, timeframe_minutes=1)
    report = inspect_series(candles, xauusd())

    assert report.duplicates == 0
    assert report.out_of_order == 0
    assert report.invalid_ohlc == 0
    assert report.inferred_minutes == 1
    # La démo tourne 24 h/24 : aucun décalage horaire ne doit être suggéré
    assert not any("UTC" in w for w in report.warnings)
