"""Accès aux données : bougies, chargement CSV, MetaTrader 5, série synthétique."""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Sequence

# Correspondance des unités de temps MT5, résolue paresseusement pour que le
# module reste importable hors Windows.
TIMEFRAMES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


@dataclass(frozen=True, slots=True)
class Candle:
    """Une bougie OHLCV clôturée."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_high(self) -> float:
        return max(self.open, self.close)

    @property
    def body_low(self) -> float:
        return min(self.open, self.close)

    @property
    def range(self) -> float:
        return self.high - self.low


def load_csv(path: str | Path) -> list[Candle]:
    """Charge un CSV OHLC.

    Accepte les en-têtes usuels (`time`/`date`/`datetime`, `open`, `high`, `low`,
    `close`, `volume`/`tick_volume`) quelle que soit la casse, ainsi que le format
    tabulé exporté par MetaTrader 5 (`<DATE>`, `<TIME>`, `<OPEN>`, ...).
    """
    raw = Path(path).read_text(encoding="utf-8-sig")
    sample = raw[:2048]
    delimiter = "\t" if "\t" in sample.splitlines()[0] else ","
    reader = csv.DictReader(raw.splitlines(), delimiter=delimiter)

    candles: list[Candle] = []
    for row in reader:
        norm = {
            (k or "").strip().strip("<>").lower(): (v or "").strip()
            for k, v in row.items()
        }
        if norm.get("date") and norm.get("time") and "open" in norm:
            stamp = f"{norm['date']} {norm['time']}"
        else:
            stamp = norm.get("time") or norm.get("datetime") or norm.get("date") or ""
        try:
            candles.append(
                Candle(
                    time=_parse_time(stamp),
                    open=float(norm["open"]),
                    high=float(norm["high"]),
                    low=float(norm["low"]),
                    close=float(norm["close"]),
                    volume=float(norm.get("tickvol") or norm.get("volume") or 0.0),
                )
            )
        except (KeyError, ValueError) as exc:  # ligne incomplète ou en-tête répété
            raise ValueError(f"Ligne CSV illisible : {row!r}") from exc

    candles.sort(key=lambda c: c.time)
    return candles


def save_csv(candles: Sequence[Candle], path: str | Path) -> None:
    """Écrit les bougies au format CSV standard."""
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "open", "high", "low", "close", "volume"])
        for c in candles:
            writer.writerow(
                [
                    c.time.strftime("%Y-%m-%d %H:%M:%S"),
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    c.volume,
                ]
            )


def _parse_time(value: str) -> datetime:
    value = value.strip().replace("T", " ")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Horodatage epoch éventuel
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"Format de date non reconnu : {value!r}") from exc


def download_mt5(
    symbol: str, timeframe: str = "M15", bars: int = 5000
) -> list[Candle]:
    """Télécharge l'historique depuis un terminal MetaTrader 5 ouvert.

    Nécessite Windows et le paquet `MetaTrader5`. L'import est volontairement
    local : le reste du bot doit rester utilisable sans MT5.
    """
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - dépend de la plateforme
        raise RuntimeError(
            "Le paquet MetaTrader5 est indisponible (Windows uniquement). "
            "Exporte tes bougies en CSV depuis MT5 et utilise --csv."
        ) from exc

    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unité de temps inconnue : {timeframe}")

    if not mt5.initialize():  # pragma: no cover - dépend de la plateforme
        raise RuntimeError(f"Connexion à MT5 impossible : {mt5.last_error()}")
    try:  # pragma: no cover - dépend de la plateforme
        tf = getattr(mt5, f"TIMEFRAME_{timeframe}")
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        if rates is None or len(rates) == 0:
            raise RuntimeError(
                f"Aucune donnée pour {symbol} en {timeframe} : {mt5.last_error()}"
            )
        return [
            Candle(
                time=datetime.fromtimestamp(int(r["time"]), tz=timezone.utc),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r["tick_volume"]),
            )
            for r in rates
        ]
    finally:  # pragma: no cover - dépend de la plateforme
        mt5.shutdown()


def synthetic_series(
    n: int = 3000,
    start: float = 1.1000,
    point: float = 0.00001,
    seed: int = 7,
    timeframe_minutes: int = 15,
) -> list[Candle]:
    """Génère une série OHLC déterministe pour les tests et la démo.

    Le processus alterne des phases de tendance et de range afin de produire des
    cassures de structure, des order blocks et des FVG exploitables — ce qu'un
    simple bruit blanc ne ferait pas.
    """
    rng = random.Random(seed)
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles: list[Candle] = []

    price = start
    drift = 0.0
    phase_left = 0
    vol = 60 * point

    for i in range(n):
        if phase_left <= 0:
            # Nouvelle phase : tendance haussière, baissière ou range
            phase_left = rng.randint(30, 90)
            regime = rng.choice(("up", "down", "range", "up", "down"))
            drift = {
                "up": 3.0 * point,
                "down": -3.0 * point,
                "range": 0.0,
            }[regime]
            vol = rng.uniform(40, 90) * point
        phase_left -= 1

        o = price
        # Marche aléatoire avec dérive, plus des chocs occasionnels qui créent
        # les déséquilibres (FVG) recherchés par la stratégie.
        shock = rng.gauss(0, vol) + drift
        if rng.random() < 0.05:
            shock *= rng.uniform(2.5, 4.5)
        c = max(point * 100, o + shock)

        wick = abs(rng.gauss(0, vol * 0.6))
        h = max(o, c) + wick * rng.uniform(0.2, 1.0)
        l = min(o, c) - wick * rng.uniform(0.2, 1.0)
        l = max(l, point * 50)

        digits = max(0, int(round(-math.log10(point))))
        candles.append(
            Candle(
                time=t0 + timedelta(minutes=timeframe_minutes * i),
                open=round(o, digits),
                high=round(h, digits),
                low=round(l, digits),
                close=round(c, digits),
                volume=rng.randint(100, 2000),
            )
        )
        price = c

    return candles


class ReplayFeed:
    """Rejoue une série de bougies, une par appel — utilisé par le paper trading."""

    def __init__(self, candles: Sequence[Candle], warmup: int = 0):
        self._candles = list(candles)
        self._i = min(warmup, len(self._candles))

    @property
    def warmup_candles(self) -> list[Candle]:
        return self._candles[: self._i]

    def __iter__(self) -> Iterator[Candle]:
        while self._i < len(self._candles):
            candle = self._candles[self._i]
            self._i += 1
            yield candle

    def next(self) -> Candle | None:
        """Renvoie la bougie suivante, ou None si la série est épuisée."""
        if self._i >= len(self._candles):
            return None
        candle = self._candles[self._i]
        self._i += 1
        return candle


class Mt5Feed:
    """Flux live : interroge MT5 et n'émet que les bougies clôturées."""

    def __init__(self, symbol: str, timeframe: str = "M15"):
        self.symbol = symbol
        self.timeframe = timeframe
        self._last_time: datetime | None = None

    def history(self, bars: int = 500) -> list[Candle]:
        """Historique de préchauffe, hors bougie en cours."""
        candles = download_mt5(self.symbol, self.timeframe, bars)
        closed = candles[:-1] if candles else []
        if closed:
            self._last_time = closed[-1].time
        return closed

    def poll(self) -> list[Candle]:
        """Renvoie les bougies clôturées depuis le dernier appel."""
        candles = download_mt5(self.symbol, self.timeframe, 50)
        closed = candles[:-1]  # la dernière bougie n'est pas terminée
        if self._last_time is None:
            fresh = closed[-1:]
        else:
            fresh = [c for c in closed if c.time > self._last_time]
        if fresh:
            self._last_time = fresh[-1].time
        return fresh
