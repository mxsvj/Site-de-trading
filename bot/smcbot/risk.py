"""Dimensionnement des positions et conversion points ↔ argent."""

from __future__ import annotations

import math

from .config import RiskConfig, SymbolSpec


def points(price_distance: float, symbol: SymbolSpec) -> float:
    """Convertit une distance de prix en points."""
    return abs(price_distance) / symbol.point


def money_per_point(lots: float, symbol: SymbolSpec) -> float:
    """Valeur monétaire d'un point pour un volume donné."""
    return lots * symbol.value_per_point_per_lot


def position_size(
    balance: float, entry: float, stop: float, risk: RiskConfig, symbol: SymbolSpec
) -> float:
    """Volume en lots tel que la perte au stop vaut `risk_pct` % du capital.

    Renvoie 0.0 si le volume calculé tombe sous le lot minimal du courtier :
    mieux vaut ne pas prendre le trade que de risquer plus que prévu.
    """
    sl_points = points(entry - stop, symbol)
    if sl_points <= 0 or balance <= 0:
        return 0.0

    risk_amount = balance * (risk.risk_pct / 100.0)
    raw_lots = risk_amount / (sl_points * symbol.value_per_point_per_lot)

    # Arrondi à l'inférieur sur le pas de volume pour ne jamais dépasser le risque
    steps = math.floor(raw_lots / symbol.lot_step + 1e-9)
    lots = steps * symbol.lot_step
    lots = min(lots, symbol.max_lot)

    if lots < symbol.min_lot:
        return 0.0
    return round(lots, 8)


def trade_pnl(
    direction: str,
    entry: float,
    exit_price: float,
    lots: float,
    symbol: SymbolSpec,
) -> float:
    """P&L net d'un trade, commissions incluses, dans la devise du compte."""
    sign = 1.0 if direction == "bullish" else -1.0
    gross_points = sign * (exit_price - entry) / symbol.point
    gross = gross_points * symbol.value_per_point_per_lot * lots
    return gross - symbol.commission_per_lot * lots


def r_multiple(direction: str, entry: float, stop: float, exit_price: float) -> float:
    """Résultat exprimé en multiple du risque initial."""
    risk_distance = abs(entry - stop)
    if risk_distance == 0:
        return 0.0
    sign = 1.0 if direction == "bullish" else -1.0
    return sign * (exit_price - entry) / risk_distance
