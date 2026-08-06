"""Configuration du bot : paramètres SMC, risque, exécution et instrument."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class SymbolSpec:
    """Caractéristiques du contrat, telles que MT5 les expose.

    `point` est le pas de cotation (0.00001 sur EURUSD 5 digits), `contract_size`
    le nominal d'un lot (100 000 unités pour une paire forex standard) et
    `value_per_point_per_lot` la valeur monétaire d'un point pour 1 lot, exprimée
    dans la devise du compte. Sur EURUSD coté en USD avec un compte USD :
    100 000 * 0.00001 = 1 USD par point et par lot.
    """

    name: str = "EURUSD"
    digits: int = 5
    point: float = 0.00001
    contract_size: float = 100_000.0
    value_per_point_per_lot: float = 1.0
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01
    # Coûts de transaction
    spread_points: float = 10.0  # spread moyen, en points
    commission_per_lot: float = 0.0  # aller-retour, devise du compte

    @property
    def pip(self) -> float:
        """Taille d'un pip (10 points sur un symbole 5 ou 3 digits)."""
        return self.point * 10 if self.digits in (3, 5) else self.point


@dataclass
class SmcConfig:
    """Paramètres de détection des concepts SMC."""

    swing_lookback: int = 3
    """Nombre de bougies de chaque côté pour valider un swing (fractale).

    Un swing n'est donc confirmé qu'avec `swing_lookback` bougies de retard :
    c'est ce qui garantit l'absence de lookahead dans le backtest.
    """

    ob_lookback: int = 12
    """Profondeur de recherche de l'order block en amont d'une cassure."""

    ob_use_body: bool = False
    """Zone de l'OB sur le corps de la bougie plutôt que sur la mèche complète."""

    ob_max_age: int = 60
    """Au-delà de N bougies, un order block non mitigé est abandonné."""

    fvg_min_points: float = 0.0
    """Taille minimale d'un FVG pour être retenu, en points (0 = pas de filtre)."""

    require_fvg: bool = True
    """N'accepter que les order blocks chevauchant un fair value gap."""

    require_sweep: bool = False
    """Exiger une prise de liquidité avant la cassure de structure."""

    sweep_lookback: int = 20
    """Fenêtre de recherche d'un balayage de liquidité avant la cassure."""

    entry_at_equilibrium: bool = False
    """Entrer à 50 % de l'order block plutôt que sur son bord proximal."""


@dataclass
class RiskConfig:
    """Paramètres de gestion du risque."""

    initial_balance: float = 10_000.0
    risk_pct: float = 0.5
    """Pourcentage du capital risqué par trade (0.5 = 0,5 %)."""

    sl_buffer_points: float = 20.0
    """Marge ajoutée sous/au-dessus de l'order block pour le stop."""

    tp_r: float = 2.0
    """Take profit exprimé en multiple du risque."""

    max_concurrent: int = 1
    """Nombre de positions ouvertes simultanément."""

    max_daily_loss_pct: float = 3.0
    """Kill switch : arrêt des prises de position pour la journée au-delà."""

    breakeven_at_r: float = 0.0
    """Passage du stop à l'entrée à N R (0 = désactivé)."""


@dataclass
class BotConfig:
    """Configuration complète, sérialisable en JSON."""

    symbol: SymbolSpec = field(default_factory=SymbolSpec)
    smc: SmcConfig = field(default_factory=SmcConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    timeframe: str = "M15"

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "BotConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            symbol=SymbolSpec(**raw.get("symbol", {})),
            smc=SmcConfig(**raw.get("smc", {})),
            risk=RiskConfig(**raw.get("risk", {})),
            timeframe=raw.get("timeframe", "M15"),
        )
