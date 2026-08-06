"""Paper trading : la stratégie tourne en continu sur un compte simulé.

Aucun ordre réel n'est envoyé. Le module lit les bougies clôturées (MT5 en live,
ou une série rejouée hors ligne), applique exactement la même stratégie et le
même courtier simulé que le backtest, journalise chaque décision et sauvegarde
l'état sur disque pour survivre à un redémarrage.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from .broker import PaperBroker, Trade
from .config import BotConfig
from .data import Candle
from .metrics import Report, build_report
from .strategy import SmcStrategy

logger = logging.getLogger("smcbot.paper")


class Feed(Protocol):
    """Source de bougies clôturées."""

    def poll(self) -> list[Candle]: ...


class _ReplayAdapter:
    """Adapte un ReplayFeed (une bougie par appel) à l'interface `poll`."""

    def __init__(self, feed: Any):
        self._feed = feed

    def poll(self) -> list[Candle]:
        candle = self._feed.next()
        return [candle] if candle is not None else []


def as_feed(feed: Any) -> Feed:
    """Normalise n'importe quelle source vers l'interface `poll`."""
    if hasattr(feed, "poll"):
        return feed
    if hasattr(feed, "next"):
        return _ReplayAdapter(feed)
    raise TypeError("La source doit exposer poll() ou next()")


class PaperTrader:
    """Boucle de trading simulé."""

    def __init__(
        self,
        cfg: BotConfig | None = None,
        state_path: str | Path | None = None,
    ):
        self.cfg = cfg or BotConfig()
        self.strategy = SmcStrategy(self.cfg)
        self.broker = PaperBroker(self.cfg)
        self.state_path = Path(state_path) if state_path else None
        self.index = 0
        self.last_time: datetime | None = None

        if self.state_path and self.state_path.exists():
            self._load_state()

    # ------------------------------------------------------------------ API

    def warmup(self, candles: Sequence[Candle]) -> None:
        """Alimente le moteur SMC en historique sans prendre de position."""
        for candle in candles:
            self.strategy.engine.push(candle)
            self.index += 1
            self.last_time = candle.time
        logger.info(
            "Préchauffe : %d bougies, biais initial = %s",
            len(candles),
            self.strategy.engine.bias or "indéfini",
        )

    def on_candle(self, candle: Candle) -> list[Trade]:
        """Traite une bougie clôturée : sorties, signal, entrée."""
        if self.last_time is not None and candle.time <= self.last_time:
            return []  # bougie déjà vue

        i = self.index
        closed = self.broker.on_candle(candle, i)
        for trade in closed:
            self._log_exit(trade)

        signal = self.strategy.on_candle(candle, can_open=self.broker.can_open)
        if signal is not None:
            before = len(self.broker.trades)
            position = self.broker.execute(signal, candle, i)
            if position is not None:
                logger.info(
                    "ENTREE %s %g lot @ %.5f | SL %.5f | TP %.5f | %s",
                    "achat" if position.direction == "bullish" else "vente",
                    position.lots,
                    position.entry,
                    position.stop,
                    position.take_profit,
                    position.reason,
                )
                # Une position peut être stoppée sur sa propre bougie d'entrée :
                # cette sortie doit apparaître dans le journal, elle aussi.
                immediate = self.broker.trades[before:]
                for trade in immediate:
                    self._log_exit(trade)
                closed = [*closed, *immediate]
            else:
                logger.warning("Signal ignoré (%s)", signal.reason)

        self.index += 1
        self.last_time = candle.time
        if self.state_path:
            self._save_state()
        return closed

    @staticmethod
    def _log_exit(trade: Trade) -> None:
        logger.info(
            "SORTIE %s %g lot @ %.5f | %s | %+.2f (%+.2fR) | solde %.2f",
            "achat" if trade.direction == "bullish" else "vente",
            trade.lots,
            trade.exit,
            trade.exit_reason,
            trade.pnl,
            trade.r,
            trade.balance_after,
        )

    def run(
        self,
        feed: Any,
        poll_seconds: float = 30.0,
        max_bars: int | None = None,
        stop_file: str | Path | None = None,
    ) -> Report:
        """Boucle principale. S'arrête sur épuisement du flux, `max_bars`
        ou apparition du fichier `stop_file` (kill switch manuel)."""
        source = as_feed(feed)
        processed = 0
        stop = Path(stop_file) if stop_file else None

        logger.info(
            "Paper trading démarré sur %s %s — solde %.2f",
            self.cfg.symbol.name,
            self.cfg.timeframe,
            self.broker.balance,
        )
        try:
            while max_bars is None or processed < max_bars:
                if stop is not None and stop.exists():
                    logger.warning("Fichier d'arrêt détecté : %s", stop)
                    break

                candles = source.poll()
                if not candles:
                    if poll_seconds <= 0:
                        break  # flux hors ligne épuisé
                    time.sleep(poll_seconds)
                    continue

                for candle in candles:
                    self.on_candle(candle)
                    processed += 1
                    if max_bars is not None and processed >= max_bars:
                        break

                if poll_seconds > 0:
                    time.sleep(poll_seconds)
        except KeyboardInterrupt:  # pragma: no cover - interaction manuelle
            logger.info("Arrêt demandé par l'utilisateur.")

        return self.report()

    def report(self) -> Report:
        return build_report(
            self.broker.trades,
            self.broker.equity_curve,
            self.cfg.risk.initial_balance,
        )

    # --------------------------------------------------------- persistance

    def _save_state(self) -> None:
        assert self.state_path is not None
        state = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "symbol": self.cfg.symbol.name,
            "timeframe": self.cfg.timeframe,
            "balance": self.broker.balance,
            "index": self.index,
            "last_time": self.last_time.isoformat() if self.last_time else None,
            "open_positions": [
                {**asdict(p), "open_time": p.open_time.isoformat()}
                for p in self.broker.positions
            ],
            "trades": [
                {
                    **asdict(t),
                    "open_time": t.open_time.isoformat(),
                    "close_time": t.close_time.isoformat(),
                }
                for t in self.broker.trades
            ],
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)  # écriture atomique

    def _load_state(self) -> None:
        assert self.state_path is not None
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("État illisible (%s), démarrage à neuf", exc)
            return

        self.broker.balance = float(state.get("balance", self.broker.balance))
        logger.info(
            "État repris : solde %.2f, %d trades archivés",
            self.broker.balance,
            len(state.get("trades", [])),
        )
        # Les positions ouvertes ne sont volontairement pas restaurées : après un
        # redémarrage, le moteur SMC est reconstruit depuis l'historique et le
        # contexte de la position (order block d'origine) n'existe plus.
        if state.get("open_positions"):
            logger.warning(
                "%d position(s) ouverte(s) au moment de l'arrêt ont été abandonnées.",
                len(state["open_positions"]),
            )


def setup_logging(log_file: str | Path | None = None, verbose: bool = True) -> None:
    """Configure la journalisation console + fichier."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
