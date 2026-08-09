"""Courtier simulé : exécution, spread, stops, commissions, kill switch.

Le même objet sert au backtest et au paper trading, de sorte qu'une session live
simulée applique exactement les règles validées en backtest.

Modélisation du spread : les bougies sont supposées en bid, comme dans MetaTrader.
Un achat s'exécute donc à l'ask (bid + spread) et un stop d'achat se déclenche au
bid ; symétriquement, une vente s'exécute au bid et son stop se déclenche à l'ask.
Le coût du spread est ainsi porté par le risque réel du trade, pas dissimulé.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .config import BotConfig
from .data import Candle
from .filters import Rejection, TradeFilters
from .risk import points, position_size, r_multiple, swap_cost, trade_pnl
from .smc import BULLISH
from .strategy import Signal


@dataclass(slots=True)
class Position:
    """Position ouverte."""

    direction: str
    entry: float
    """Prix d'exécution réel (ask à l'achat, bid à la vente)."""

    stop: float
    take_profit: float
    lots: float
    open_index: int
    open_time: datetime
    reason: str
    initial_stop: float
    breakeven_done: bool = False

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.initial_stop)


@dataclass(slots=True)
class Trade:
    """Position clôturée."""

    direction: str
    entry: float
    exit: float
    stop: float
    take_profit: float
    lots: float
    open_time: datetime
    close_time: datetime
    open_index: int
    close_index: int
    pnl: float
    swap: float
    r: float
    exit_reason: str
    reason: str
    balance_after: float

    @property
    def won(self) -> bool:
        return self.pnl > 0


@dataclass(slots=True)
class EquityPoint:
    time: datetime
    balance: float
    equity: float


class PaperBroker:
    """Compte simulé : une seule source de vérité pour le P&L."""

    def __init__(self, cfg: BotConfig | None = None):
        self.cfg = cfg or BotConfig()
        self.balance = self.cfg.risk.initial_balance
        self.positions: list[Position] = []
        self.trades: list[Trade] = []
        self.equity_curve: list[EquityPoint] = []
        self.rejected: list[str] = []
        self.filters = TradeFilters(self.cfg.filters, self.cfg.symbol)
        self.skipped: dict[str, int] = {}
        """Compte des setups écartés par motif — utile pour régler les filtres."""

        self._day: date | None = None
        self._day_start_balance = self.balance
        self._day_locked = False
        self._day_trades = 0
        self._session_open = True

    # ------------------------------------------------------------------ API

    @property
    def spread(self) -> float:
        return self.cfg.symbol.spread_points * self.cfg.symbol.point

    @property
    def can_open(self) -> bool:
        max_daily = self.cfg.filters.max_trades_per_day
        return (
            not self._day_locked
            and self._session_open
            and len(self.positions) < self.cfg.risk.max_concurrent
            and (max_daily <= 0 or self._day_trades < max_daily)
        )

    def equity(self, candle: Candle) -> float:
        """Capital incluant le résultat latent, valorisé à la clôture."""
        floating = 0.0
        for pos in self.positions:
            exit_price = (
                candle.close if pos.direction == BULLISH else candle.close + self.spread
            )
            floating += trade_pnl(
                pos.direction, pos.entry, exit_price, pos.lots, self.cfg.symbol
            )
        return self.balance + floating

    def on_candle(self, candle: Candle, index: int) -> list[Trade]:
        """Gère la journée de trading et les sorties, avant tout nouveau signal."""
        self._roll_day(candle)
        self._session_open = self.filters.session_allows(candle.time)
        closed = self._check_exits(candle, index)
        closed += self._apply_time_exit(candle, index)
        self._apply_breakeven(candle)
        self.equity_curve.append(
            EquityPoint(candle.time, self.balance, self.equity(candle))
        )
        return closed

    def execute(self, signal: Signal, candle: Candle, index: int) -> Position | None:
        """Ouvre la position correspondant au signal, si le risque le permet."""
        if not self.can_open:
            return None

        entry = self._fill_price(signal, candle)
        stop = signal.stop
        risk_distance = abs(entry - stop)
        if risk_distance <= 0:
            self._reject(candle, "stop invalide")
            return None

        # Filtres de coût : ils portent sur le risque réel, spread inclus.
        refus = self.filters.check_trade(points(risk_distance, self.cfg.symbol))
        if refus is not None:
            self._reject(candle, refus)
            return None

        lots = position_size(
            self.balance, entry, stop, self.cfg.risk, self.cfg.symbol
        )
        if lots <= 0:
            self._reject(candle, "volume sous le lot minimal (risque trop faible)")
            return None

        if signal.direction == BULLISH:
            take_profit = entry + signal.tp_r * risk_distance
        else:
            take_profit = entry - signal.tp_r * risk_distance

        pos = Position(
            direction=signal.direction,
            entry=entry,
            stop=stop,
            take_profit=take_profit,
            lots=lots,
            open_index=index,
            open_time=candle.time,
            reason=signal.reason,
            initial_stop=stop,
        )
        self.positions.append(pos)
        self._day_trades += 1

        # Une position ouverte en cours de bougie peut être stoppée sur cette
        # même bougie. En revanche on ne lui accorde pas le take profit : rien
        # ne dit que l'extrême favorable de la bougie s'est produit après
        # l'entrée. Hypothèse volontairement défavorable.
        self._check_exits(candle, index, only=pos, sl_only=True)
        return pos

    def close_all(self, candle: Candle, index: int, reason: str = "fin") -> list[Trade]:
        closed = []
        for pos in list(self.positions):
            price = (
                candle.close if pos.direction == BULLISH else candle.close + self.spread
            )
            closed.append(self._close(pos, price, candle, index, reason))
        return closed

    # -------------------------------------------------------------- interne

    def _fill_price(self, signal: Signal, candle: Candle) -> float:
        """Prix d'exécution, selon la nature de l'ordre.

        Un ordre **limite** est posé avant que la bougie ne se forme : si elle
        ouvre au-delà du niveau, le remplissage se fait à l'ouverture, meilleur
        prix, et c'est légitime.

        Un ordre **au marché** est décidé à la clôture de la bougie. Le remplir
        à son ouverture reviendrait à entrer avant d'avoir eu le signal — un
        lookahead qui flatte l'entrée, rapproche le stop, gonfle le volume et
        rapproche le take profit. Le seul prix honnête est la clôture.
        """
        if signal.entry_type == "market":
            if signal.direction == BULLISH:
                return candle.close + self.spread
            return candle.close

        level = signal.entry_level
        if signal.direction == BULLISH:
            # Achat : déclenché quand le bid touche le niveau, exécuté à l'ask.
            bid_fill = min(level, candle.open) if candle.open < level else level
            return bid_fill + self.spread
        # Vente : déclenchée quand le bid atteint le niveau, exécutée au bid.
        return max(level, candle.open) if candle.open > level else level

    def _check_exits(
        self,
        candle: Candle,
        index: int,
        only: Position | None = None,
        sl_only: bool = False,
    ) -> list[Trade]:
        closed: list[Trade] = []
        targets = [only] if only is not None else list(self.positions)

        for pos in targets:
            if pos not in self.positions:
                continue
            if pos.direction == BULLISH:
                hit_sl = candle.low <= pos.stop
                hit_tp = candle.high >= pos.take_profit
            else:
                hit_sl = candle.high + self.spread >= pos.stop
                hit_tp = candle.low + self.spread <= pos.take_profit
            if sl_only:
                hit_tp = False

            # Si les deux niveaux sont dans la bougie, on ne connaît pas l'ordre
            # de passage : on retient le stop.
            if hit_sl:
                closed.append(self._close(pos, pos.stop, candle, index, "SL"))
            elif hit_tp:
                closed.append(self._close(pos, pos.take_profit, candle, index, "TP"))

        return closed

    def _close(
        self,
        pos: Position,
        price: float,
        candle: Candle,
        index: int,
        exit_reason: str,
    ) -> Trade:
        pnl = trade_pnl(pos.direction, pos.entry, price, pos.lots, self.cfg.symbol)
        portage = swap_cost(
            pos.direction, pos.lots, pos.open_time, candle.time, self.cfg.symbol
        )
        pnl += portage
        self.balance += pnl
        self.positions.remove(pos)

        trade = Trade(
            direction=pos.direction,
            entry=pos.entry,
            exit=price,
            stop=pos.initial_stop,
            take_profit=pos.take_profit,
            lots=pos.lots,
            open_time=pos.open_time,
            close_time=candle.time,
            open_index=pos.open_index,
            close_index=index,
            pnl=pnl,
            swap=portage,
            r=r_multiple(pos.direction, pos.entry, pos.initial_stop, price),
            exit_reason=exit_reason,
            reason=pos.reason,
            balance_after=self.balance,
        )
        self.trades.append(trade)
        self._check_daily_limit()
        return trade

    def _apply_time_exit(self, candle: Candle, index: int) -> list[Trade]:
        """Clôture les positions qui durent depuis trop de bougies.

        Appliquée **après** les stops et les objectifs : si le prix a touché
        l'un des deux pendant la bougie, c'est lui qui a clôturé la position,
        pas l'horloge. Sortir sur le temps d'abord reviendrait à s'accorder un
        prix de clôture alors que le stop avait déjà sauté.

        La sortie se fait à la clôture de la bougie, au prix du marché — donc
        en payant le spread comme n'importe quelle sortie discrétionnaire.
        """
        limite = self.cfg.risk.max_bars_in_trade
        if limite <= 0:
            return []

        closed: list[Trade] = []
        for pos in list(self.positions):
            if index - pos.open_index < limite:
                continue
            price = (
                candle.close if pos.direction == BULLISH else candle.close + self.spread
            )
            closed.append(self._close(pos, price, candle, index, "temps"))
        return closed

    def _apply_breakeven(self, candle: Candle) -> None:
        """Remonte le stop à l'entrée une fois le seuil en R atteint.

        Appliqué en fin de bougie : le stop modifié ne vaut qu'à partir de la
        bougie suivante, ce qui évite de supposer l'ordre des évènements intrabar.
        """
        trigger_r = self.cfg.risk.breakeven_at_r
        if trigger_r <= 0:
            return

        for pos in self.positions:
            if pos.breakeven_done:
                continue
            distance = pos.risk_distance
            if pos.direction == BULLISH:
                reached = candle.high >= pos.entry + trigger_r * distance
            else:
                reached = candle.low + self.spread <= pos.entry - trigger_r * distance
            if reached:
                pos.stop = pos.entry
                pos.breakeven_done = True

    def _reject(self, candle: Candle, motif: Rejection | str) -> None:
        code = motif.code if isinstance(motif, Rejection) else motif
        self.rejected.append(f"{candle.time:%Y-%m-%d %H:%M} : {motif}")
        self.skipped[code] = self.skipped.get(code, 0) + 1

    def _roll_day(self, candle: Candle) -> None:
        day = candle.time.date()
        if self._day != day:
            self._day = day
            self._day_start_balance = self.balance
            self._day_locked = False
            self._day_trades = 0

    def _check_daily_limit(self) -> None:
        limit = self.cfg.risk.max_daily_loss_pct
        if limit <= 0 or self._day_start_balance <= 0:
            return
        drawdown_pct = (
            (self._day_start_balance - self.balance) / self._day_start_balance * 100.0
        )
        if drawdown_pct >= limit:
            self._day_locked = True
