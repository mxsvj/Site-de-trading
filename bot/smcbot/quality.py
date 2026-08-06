"""Contrôle de qualité d'une série de bougies avant tout backtest.

Un backtest sur des données trouées, dupliquées ou horodatées dans le fuseau du
serveur du courtier produit des chiffres parfaitement présentables et faux. Ce
module cherche les défauts qui ne se voient pas à l'œil nu :

- décalage horaire du serveur MT5 (la cause la plus fréquente de filtres de
  session qui portent sur les mauvaises heures) ;
- trous, doublons et horodatages désordonnés ;
- incohérence entre le `point` configuré et les décimales réellement présentes ;
- amplitude typique des bougies, pour vérifier que le plancher de stop a du sens.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

from .config import SymbolSpec
from .data import Candle

# Pause quotidienne du marché de l'or et du forex : 21:00-22:00 UTC (l'heure
# creuse observée sert de repère pour deviner le fuseau du serveur).
QUIET_HOUR_UTC = 21

# Une vraie pause quotidienne vide l'heure concernée. En deçà de cette fraction
# du volume horaire médian, on considère la pause avérée ; au-dessus, la
# distribution est trop plate pour conclure quoi que ce soit.
PAUSE_RATIO = 0.5


@dataclass
class DataReport:
    """Diagnostic d'une série de bougies."""

    bars: int = 0
    start: datetime | None = None
    end: datetime | None = None
    inferred_minutes: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    saturday_bars: int = 0
    sunday_bars: int = 0
    quiet_hour: int = -1
    suggested_tz_shift: int = 0
    gaps: list[tuple[datetime, int]] = field(default_factory=list)
    median_range_points: float = 0.0
    p90_range_points: float = 0.0
    flat_bars: int = 0
    invalid_ohlc: int = 0
    decimals_seen: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.warnings

    def to_text(self) -> str:
        lignes = [
            "┌─ Qualité des données ──────────────────────",
            f"│ Bougies             : {self.bars:,}",
        ]
        if self.start and self.end:
            lignes.append(
                f"│ Période             : {self.start:%Y-%m-%d %H:%M} → "
                f"{self.end:%Y-%m-%d %H:%M}"
            )
        lignes += [
            f"│ Unité de temps      : M{self.inferred_minutes} (déduite)",
            f"│ Décimales observées : {self.decimals_seen}",
            f"│ Amplitude médiane   : {self.median_range_points:.0f} points "
            f"(90e centile : {self.p90_range_points:.0f})",
            "├─ Intégrité ────────────────────────────────",
            f"│ Doublons            : {self.duplicates}",
            f"│ Hors séquence       : {self.out_of_order}",
            f"│ Bougies plates      : {self.flat_bars}",
            f"│ OHLC incohérent     : {self.invalid_ohlc}",
            f"│ Trous intra-session : {len(self.gaps)}",
            "├─ Horaires ─────────────────────────────────",
            f"│ Heure creuse        : "
            + (f"{self.quiet_hour:02d}h" if self.quiet_hour >= 0 else "indéterminée"),
            f"│ Décalage supposé    : "
            + (
                f"UTC{self.suggested_tz_shift:+d}"
                if self.quiet_hour >= 0
                else "non déductible"
            ),
            f"│ Bougies samedi      : {self.saturday_bars}",
            f"│ Bougies dimanche    : {self.sunday_bars}",
            "└────────────────────────────────────────────",
        ]

        if self.gaps:
            lignes.append("\nPlus longs trous :")
            for quand, minutes in self.gaps[:5]:
                lignes.append(f"  {quand:%Y-%m-%d %H:%M} → {minutes} min manquantes")

        if self.warnings:
            lignes.append("\n⚠ À traiter avant de tirer la moindre conclusion :")
            lignes += [f"  - {w}" for w in self.warnings]
        else:
            lignes.append("\nAucun défaut détecté.")

        return "\n".join(lignes)


def inspect_series(
    candles: Sequence[Candle], symbol: SymbolSpec | None = None
) -> DataReport:
    """Analyse une série et renvoie son diagnostic."""
    symbol = symbol or SymbolSpec()
    report = DataReport(bars=len(candles))
    if not candles:
        report.warnings.append("série vide")
        return report

    report.start, report.end = candles[0].time, candles[-1].time

    deltas: list[int] = []
    for previous, current in zip(candles, candles[1:]):
        minutes = int((current.time - previous.time).total_seconds() // 60)
        if minutes == 0:
            report.duplicates += 1
        elif minutes < 0:
            report.out_of_order += 1
        else:
            deltas.append(minutes)

    report.inferred_minutes = int(statistics.median(deltas)) if deltas else 0

    # Répartition horaire : la pause quotidienne du marché révèle le fuseau du
    # serveur. Encore faut-il qu'elle existe — un flux continu 24 h/24 n'a pas
    # d'heure creuse, et prendre le minimum d'une distribution plate reviendrait
    # à suggérer un décalage au hasard.
    presentes = Counter(c.time.hour for c in candles)
    # Les 24 heures doivent figurer explicitement : une heure entièrement vide
    # n'apparaît pas dans un Counter, et c'est justement celle qu'on cherche.
    par_heure = {h: presentes.get(h, 0) for h in range(24)}
    couvertes = len({c.time.date() for c in candles})
    if couvertes >= 3 and len(presentes) >= 12:
        creuse = min(par_heure, key=lambda h: par_heure[h])
        mediane = statistics.median(par_heure.values())
        if mediane > 0 and par_heure[creuse] < PAUSE_RATIO * mediane:
            report.quiet_hour = creuse
            brut = (creuse - QUIET_HOUR_UTC) % 24
            report.suggested_tz_shift = brut - 24 if brut > 12 else brut

    # Trous : un écart supérieur au double du pas. La coupure du week-end et la
    # pause quotidienne sont normales — les compter en défauts noierait les
    # vrais trous sous une ligne par jour.
    if report.inferred_minutes > 0:
        seuil = report.inferred_minutes * 2
        trous: list[tuple[datetime, int]] = []
        for previous, current in zip(candles, candles[1:]):
            minutes = int((current.time - previous.time).total_seconds() // 60)
            if minutes <= seuil:
                continue
            if _spans_weekend(previous.time, current.time):
                continue
            if _spans_daily_break(previous.time, current.time, report.quiet_hour):
                continue
            trous.append((previous.time, minutes - report.inferred_minutes))
        trous.sort(key=lambda g: g[1], reverse=True)
        report.gaps = trous

    # Intégrité des bougies
    for candle in candles:
        if candle.high < candle.low or not (
            candle.low <= candle.open <= candle.high
            and candle.low <= candle.close <= candle.high
        ):
            report.invalid_ohlc += 1
        if candle.high == candle.low:
            report.flat_bars += 1

    ranges = [(c.high - c.low) / symbol.point for c in candles]
    report.median_range_points = statistics.median(ranges)
    report.p90_range_points = _percentile(ranges, 0.90)

    report.decimals_seen = max(_decimals(c.close) for c in candles)

    report.saturday_bars = sum(1 for c in candles if c.time.weekday() == 5)
    report.sunday_bars = sum(1 for c in candles if c.time.weekday() == 6)

    _add_warnings(report, symbol)
    return report


def shift_times(candles: Sequence[Candle], hours: float) -> list[Candle]:
    """Décale tous les horodatages, pour ramener une série serveur en UTC."""
    delta = timedelta(hours=hours)
    return [
        Candle(c.time + delta, c.open, c.high, c.low, c.close, c.volume)
        for c in candles
    ]


# --------------------------------------------------------------------- interne


def _add_warnings(report: DataReport, symbol: SymbolSpec) -> None:
    if report.duplicates:
        report.warnings.append(
            f"{report.duplicates} horodatage(s) en double — le moteur traitera "
            f"deux fois la même bougie"
        )
    if report.out_of_order:
        report.warnings.append(
            f"{report.out_of_order} bougie(s) hors séquence — trie le fichier par date"
        )
    if report.invalid_ohlc:
        report.warnings.append(
            f"{report.invalid_ohlc} bougie(s) au OHLC incohérent (open ou close "
            f"hors du range)"
        )
    if report.suggested_tz_shift != 0:
        report.warnings.append(
            f"les horodatages semblent en UTC{report.suggested_tz_shift:+d} et non "
            f"en UTC : les filtres de session porteraient sur les mauvaises heures. "
            f"Corrige avec --tz-shift {-report.suggested_tz_shift}"
        )
    if report.saturday_bars:
        report.warnings.append(
            f"{report.saturday_bars} bougie(s) un samedi — marché normalement fermé"
        )
    if len(report.gaps) > max(10, report.bars // 500):
        report.warnings.append(
            f"{len(report.gaps)} trous intra-session — historique incomplet, les "
            f"bougies supérieures reconstruites seront fausses sur ces plages"
        )
    if report.decimals_seen > symbol.digits:
        report.warnings.append(
            f"les prix ont {report.decimals_seen} décimales alors que le symbole en "
            f"déclare {symbol.digits} — vérifie `point` et `digits`"
        )
    if report.flat_bars > report.bars // 20:
        report.warnings.append(
            f"{report.flat_bars} bougies sans amplitude — données de faible qualité "
            f"ou instrument peu liquide"
        )


def _spans_weekend(start: datetime, end: datetime) -> bool:
    """L'écart traverse-t-il la coupure hebdomadaire ?"""
    return start.weekday() == 4 or end.weekday() == 6 or end.weekday() < start.weekday()


def _spans_daily_break(start: datetime, end: datetime, quiet_hour: int) -> bool:
    """L'écart correspond-il à la pause quotidienne du marché ?

    L'heure de référence est celle réellement observée, pas 21:00 UTC : sur un
    export horodaté à l'heure du serveur, la pause tombe ailleurs.
    """
    if quiet_hour < 0:
        return False
    if (end - start) > timedelta(hours=3):
        return False  # trop long pour être la seule pause quotidienne
    heure = start
    while heure < end:
        if heure.hour == quiet_hour:
            return True
        heure += timedelta(hours=1)
    return end.hour == quiet_hour


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(q * (len(ordered) - 1)))
    return ordered[index]


def _decimals(value: float) -> int:
    texte = f"{value:.10f}".rstrip("0")
    return len(texte.partition(".")[2])
