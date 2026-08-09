"""Accès aux données : bougies, chargement CSV, MetaTrader 5, série synthétique."""

from __future__ import annotations

import csv
import math
import os
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


# Codes d'erreur renvoyés par mt5.initialize(), avec ce qu'il faut en faire.
MT5_ERREURS = {
    -6: (
        "le terminal refuse l'authentification",
        "MT5 est ouvert mais aucun compte n'y est connecté, ou bien "
        "`initialize()` a ouvert un AUTRE terminal que le tien. "
        "Vérifie que ton MT5 affiche bien ton compte en haut à gauche, puis "
        "réessaie avec --mt5-path pour désigner le bon terminal.",
    ),
    -8: (
        "trading algorithmique désactivé",
        "Outils → Options → Expert Advisors → coche « Autoriser le trading "
        "algorithmique ». (smcbot n'envoie aucun ordre, mais l'API l'exige.)",
    ),
    -4: (
        "terminal introuvable",
        "Indique son chemin avec --mt5-path "
        r'"C:\Program Files\MetaTrader 5\terminal64.exe".',
    ),
    -2: (
        "paramètres refusés par le terminal",
        "Souvent le nombre de bougies demandé. Augmente la limite dans MT5 "
        "(Outils → Options → Graphiques → « Max. de barres dans le graphique ») "
        "ou demande moins de bougies.",
    ),
    -10005: (
        "le terminal ne répond pas",
        "Ferme complètement MT5, rouvre-le, attends qu'il soit connecté, "
        "puis relance la commande.",
    ),
}


def terminaux_installes() -> list[Path]:
    """Cherche les terminaux MT5 installés, pour aider à désigner le bon."""
    racines = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    trouves: list[Path] = []
    for racine in racines:
        try:
            if not racine.is_dir():
                continue
            for dossier in racine.iterdir():
                exe = dossier / "terminal64.exe"
                if exe.is_file() and exe not in trouves:
                    trouves.append(exe)
        except OSError:
            continue
    return trouves


def init_mt5(mt5, path: str | None = None) -> None:
    """Ouvre la connexion au terminal, avec un message utile en cas d'échec.

    Les identifiants ne sont jamais passés en ligne de commande : s'ils sont
    nécessaires, ils sont lus dans les variables d'environnement MT5_LOGIN,
    MT5_PASSWORD et MT5_SERVER, et ne sont ni affichés ni journalisés.
    """
    options: dict = {}
    if path:
        options["path"] = path

    login = os.environ.get("MT5_LOGIN")
    mot_de_passe = os.environ.get("MT5_PASSWORD")
    serveur = os.environ.get("MT5_SERVER")
    if login and mot_de_passe and serveur:
        try:
            options["login"] = int(login)
        except ValueError as exc:
            raise RuntimeError("MT5_LOGIN doit être le numéro de compte.") from exc
        options["password"] = mot_de_passe
        options["server"] = serveur

    if mt5.initialize(**options):
        return

    code, message = _erreur_mt5(mt5)
    resume, conseil = MT5_ERREURS.get(code, ("connexion impossible", ""))
    detail = f"Connexion à MT5 impossible — {resume} (code {code}: {message})"

    if conseil:
        detail += f"\n{conseil}"
    if code == -6 and not (login and mot_de_passe and serveur):
        detail += (
            "\nEn dernier recours, donne les identifiants par variables "
            "d'environnement (jamais en ligne de commande) :\n"
            "  set MT5_LOGIN=123456\n"
            "  set MT5_PASSWORD=ton_mot_de_passe\n"
            '  set MT5_SERVER="NomDuServeur"'
        )

    installes = terminaux_installes()
    if installes and code in (-6, -4):
        liste = "\n".join(f"  --mt5-path \"{p}\"" for p in installes[:5])
        detail += f"\nTerminaux détectés sur cette machine :\n{liste}"

    raise RuntimeError(detail)


# Une seule requête au-delà de quelques dizaines de milliers de bougies est
# refusée par le terminal (« Invalid params »). On découpe donc la demande.
TAILLE_TRANCHE = 20_000


def _copier_par_tranches(mt5, symbol: str, tf, bars: int) -> list:
    """Récupère l'historique par tranches, en remontant le temps.

    `copy_rates_from_pos` plafonne à ce que le terminal accepte de servir d'un
    coup — plafond qui dépend du réglage « Max. de barres dans le graphique ».
    Demander 200 000 bougies d'un bloc échoue là où vingt demandes de 10 000
    réussissent.
    """
    from datetime import timedelta

    collecte: list = []
    ancre = datetime.now(timezone.utc)

    while len(collecte) < bars:
        voulu = min(TAILLE_TRANCHE, bars - len(collecte))
        tranche = mt5.copy_rates_from(symbol, tf, ancre, voulu)
        if tranche is None or len(tranche) == 0:
            break

        collecte = list(tranche) + collecte
        plus_ancienne = datetime.fromtimestamp(int(tranche[0]["time"]), tz=timezone.utc)
        if len(collecte) < bars:
            print(f"  {len(collecte)} bougies récupérées (jusqu'au {plus_ancienne:%Y-%m-%d})...")
        ancre = plus_ancienne - timedelta(seconds=1)

        if len(tranche) < voulu:
            break  # le courtier n'a pas plus d'historique

    # Les tranches peuvent se chevaucher d'une bougie : on déduplique.
    par_temps = {int(r["time"]): r for r in collecte}
    return [par_temps[t] for t in sorted(par_temps)]


def _erreur_mt5(mt5) -> tuple[int, str]:
    """Normalise le retour de last_error() en (code, message)."""
    brut = mt5.last_error()
    if isinstance(brut, (tuple, list)) and len(brut) >= 2:
        return int(brut[0]), str(brut[1])
    return 0, str(brut)


def download_mt5(
    symbol: str, timeframe: str = "M15", bars: int = 5000, path: str | None = None
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

    init_mt5(mt5, path)
    try:  # pragma: no cover - dépend de la plateforme
        tf = getattr(mt5, f"TIMEFRAME_{timeframe}")
        if not mt5.symbol_select(symbol, True):
            connus = mt5.symbols_get(f"*{symbol[:3]}*") or []
            proches = ", ".join(s.name for s in connus[:8]) or "aucun"
            raise RuntimeError(
                f"Symbole {symbol} introuvable chez ton courtier.\n"
                f"Les noms varient (XAUUSD, XAUUSD.a, GOLD...). Symboles "
                f"approchants : {proches}"
            )
        rates = _copier_par_tranches(mt5, symbol, tf, bars)
        if not rates:
            raise RuntimeError(
                f"Aucune donnée pour {symbol} en {timeframe} : {mt5.last_error()}\n"
                f"Ouvre un graphique {symbol} sur cette unité de temps et fais "
                f"défiler vers la gauche pour charger l'historique."
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


def spec_depuis_mt5(mt5, symbol: str) -> dict:
    """Extrait la spécification d'un symbole, comme mt5/ExportBars.mq5.

    Reproduit délibérément la même logique et les mêmes noms de champs que le
    script MQL5, pour que les deux chemins d'export donnent des fichiers
    interchangeables.

    Deux valeurs demandent de l'attention :

    - `value_per_point_per_lot` conditionne tout le dimensionnement. MT5 expose
      la valeur d'un *tick*, pas d'un point ; sur la plupart des symboles les
      deux coïncident, mais pas tous, d'où la conversion par `point / tick_size`.
    - les swaps ne sont reportés que si le courtier les exprime en points
      (`swap_mode == 1`). Dans les autres modes on écrit 0 plutôt qu'un chiffre
      dans la mauvaise unité, qui se propagerait silencieusement.
    """
    spec = mt5.symbol_info(symbol)
    if spec is None:  # pragma: no cover - dépend de la plateforme
        raise RuntimeError(f"Spécification indisponible pour {symbol}.")

    point = float(spec.point)
    tick_size = float(getattr(spec, "trade_tick_size", 0.0) or 0.0)
    tick_value = float(getattr(spec, "trade_tick_value", 0.0) or 0.0)
    valeur_point = tick_value * (point / tick_size) if tick_size > 0 else tick_value

    en_points = int(getattr(spec, "swap_mode", 0)) == 1
    return {
        "name": spec.name,
        "digits": int(spec.digits),
        "point": point,
        "contract_size": float(spec.trade_contract_size),
        "value_per_point_per_lot": valeur_point,
        "min_lot": float(spec.volume_min),
        "max_lot": float(spec.volume_max),
        "lot_step": float(spec.volume_step),
        "spread_points": float(spec.spread),
        "swap_long_points": float(spec.swap_long) if en_points else 0.0,
        "swap_short_points": float(spec.swap_short) if en_points else 0.0,
        "commission_per_lot": 0.0,
    }


def market_open(when: datetime) -> bool:
    """Le marché forex/or est-il ouvert à cet instant (en UTC) ?

    Ouverture le dimanche 22:00 UTC, clôture le vendredi 21:00 UTC, avec une
    pause quotidienne de 21:00 à 22:00.
    """
    if when.weekday() == 5:  # samedi
        return False
    if when.weekday() == 6 and when.hour < 22:  # dimanche avant l'ouverture
        return False
    if when.weekday() == 4 and when.hour >= 21:  # vendredi après la clôture
        return False
    return when.hour != 21  # pause quotidienne


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

    Les horodatages suivent le calendrier réel du marché : ni samedi, ni pause
    quotidienne. Une série de démonstration qui coterait 24 h/24 donnerait une
    fausse idée du comportement des filtres horaires.
    """
    rng = random.Random(seed)
    step = timedelta(minutes=timeframe_minutes)
    moment = datetime(2024, 1, 1, tzinfo=timezone.utc)  # lundi, marché ouvert
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
                time=moment,
                open=round(o, digits),
                high=round(h, digits),
                low=round(l, digits),
                close=round(c, digits),
                volume=rng.randint(100, 2000),
            )
        )
        price = c

        moment += step
        while not market_open(moment):
            moment += step

    return candles


class Resampler:
    """Agrège des bougies en une unité de temps supérieure, sans lookahead.

    Une bougie supérieure n'est émise qu'à l'arrivée de la première bougie du
    palier suivant : au moment où le moteur la reçoit, elle est réellement
    terminée. Le biais issu de l'unité supérieure accuse donc le retard qu'il
    aurait en direct — c'est voulu.
    """

    def __init__(self, minutes: int):
        if minutes <= 0:
            raise ValueError("La période de rééchantillonnage doit être positive.")
        self.minutes = minutes
        self._bucket: int | None = None
        self._open = self._high = self._low = self._close = 0.0
        self._volume = 0.0
        self._start: datetime | None = None

    def push(self, candle: Candle) -> list[Candle]:
        """Ajoute une bougie et renvoie la bougie supérieure achevée, s'il y en a."""
        bucket = self._bucket_of(candle.time)
        done: list[Candle] = []

        if self._bucket is None:
            self._start_bucket(bucket, candle)
        elif bucket != self._bucket:
            done.append(self._flush())
            self._start_bucket(bucket, candle)
        else:
            self._high = max(self._high, candle.high)
            self._low = min(self._low, candle.low)
            self._close = candle.close
            self._volume += candle.volume

        return done

    @property
    def pending(self) -> Candle | None:
        """Bougie supérieure en cours de formation (jamais transmise au moteur)."""
        if self._bucket is None or self._start is None:
            return None
        return Candle(
            self._start, self._open, self._high, self._low, self._close, self._volume
        )

    def _bucket_of(self, when: datetime) -> int:
        minutes = int(when.timestamp()) // 60
        return (minutes // self.minutes) * self.minutes

    def _start_bucket(self, bucket: int, candle: Candle) -> None:
        self._bucket = bucket
        self._start = datetime.fromtimestamp(bucket * 60, tz=timezone.utc)
        self._open, self._high = candle.open, candle.high
        self._low, self._close = candle.low, candle.close
        self._volume = candle.volume

    def _flush(self) -> Candle:
        assert self._start is not None
        return Candle(
            self._start, self._open, self._high, self._low, self._close, self._volume
        )


def resample(candles: Sequence[Candle], minutes: int) -> list[Candle]:
    """Version non incrémentale du rééchantillonnage (tests, analyses)."""
    sampler = Resampler(minutes)
    out: list[Candle] = []
    for candle in candles:
        out.extend(sampler.push(candle))
    if sampler.pending is not None:
        out.append(sampler.pending)  # dernière bougie, éventuellement incomplète
    return out


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

    def __init__(self, symbol: str, timeframe: str = "M15", path: str | None = None):
        self.symbol = symbol
        self.timeframe = timeframe
        self.path = path
        self._last_time: datetime | None = None

    def history(self, bars: int = 500) -> list[Candle]:
        """Historique de préchauffe, hors bougie en cours."""
        candles = download_mt5(self.symbol, self.timeframe, bars, self.path)
        closed = candles[:-1] if candles else []
        if closed:
            self._last_time = closed[-1].time
        return closed

    def poll(self) -> list[Candle]:
        """Renvoie les bougies clôturées depuis le dernier appel."""
        candles = download_mt5(self.symbol, self.timeframe, 50, self.path)
        closed = candles[:-1]  # la dernière bougie n'est pas terminée
        if self._last_time is None:
            fresh = closed[-1:]
        else:
            fresh = [c for c in closed if c.time > self._last_time]
        if fresh:
            self._last_time = fresh[-1].time
        return fresh
