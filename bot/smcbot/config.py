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
    swap_long_points: float = 0.0
    swap_short_points: float = 0.0
    """Frais de portage par nuit et par lot, en points. Négatif = coût.

    Se lisent dans MT5 (Spécification du symbole, « Swap long / court »). Sur
    l'or ils sont rarement négligeables, et une stratégie qui garde ses
    positions plusieurs jours les paie à chaque nuit — triple le mercredi, qui
    couvre le week-end.
    """

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

    require_htf_zone: bool = False
    """N'entrer que si la zone recoupe un order block ou un FVG de l'unité de
    temps supérieure. Sans effet si `htf` n'est pas configuré."""


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

    limit_fill_margin_points: float = 1.0
    """Traversée exigée d'un niveau pour qu'un ordre limite soit rempli, en points.

    Un ordre limite ne se remplit pas parce que le prix a effleuré son niveau :
    il faut que le marché y traite assez de volume pour purger la file d'attente
    devant nous. Sans cette marge, le moteur accorde un remplissage certain, en
    totalité, au meilleur prix de l'excursion — dès que le plus bas de la bougie
    touche le niveau au centième près.

    Ce biais flatte exactement les entrées limite, donc toute comparaison entre
    entrée limite et entrée au marché serait faussée en faveur de la première.

    1 point est le minimum qui ait un sens : le prix doit avoir réellement coté
    au-delà du niveau, pas seulement l'avoir touché. Ce n'est pas pour autant
    une valeur mesurée — la vraie probabilité de remplissage au plus bas d'une
    bougie est bien inférieure à 1. Avant de conclure quoi que ce soit sur une
    stratégie à entrée limite, faire varier ce paramètre et regarder si le
    verdict tient."""

    max_bars_in_trade: int = 0
    """Sortie sur le temps : clôture au marché après N bougies (0 = désactivé).

    En scalping, un setup qui n'a pas travaillé rapidement est généralement
    invalidé : le conserver revient à porter le risque sans l'espérance qui le
    justifiait. C'est une sortie sur le temps écoulé, pas sur le prix — elle
    peut donc clôturer en perte comme en gain."""


@dataclass
class FilterConfig:
    """Filtres d'admissibilité d'un trade — décisifs en scalping.

    Ils ne cherchent pas de setup : ils écartent ceux dont l'espérance est
    mangée d'avance par les frais, l'heure ou les conditions de marché.
    """

    sessions: list[str] = field(default_factory=list)
    """Plages horaires UTC autorisées, ex. ["07:00-11:00", "13:00-17:00"].

    Liste vide = aucune restriction. Attention : les heures sont en UTC, pas
    dans le fuseau du serveur MT5 (souvent UTC+2 ou UTC+3).
    """

    weekdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    """Jours autorisés, 0 = lundi. Le vendredi soir et le dimanche sont
    généralement à éviter (spread large, liquidité absente)."""

    max_spread_points: float = 0.0
    """Spread maximal toléré à l'entrée, en points (0 = pas de limite)."""

    max_cost_ratio: float = 0.0
    """Part maximale du risque absorbée par les frais (0.30 = 30 %, 0 = off).

    C'est le garde-fou central du scalping : il refuse mécaniquement les
    setups dont le stop est trop serré pour absorber spread et commission.
    """

    min_stop_points: float = 0.0
    """Distance minimale entrée→stop, en points (0 = pas de minimum)."""

    max_trades_per_day: int = 0
    """Plafond de trades par jour (0 = illimité). Anti-surtrading."""


@dataclass
class BotConfig:
    """Configuration complète, sérialisable en JSON."""

    symbol: SymbolSpec = field(default_factory=SymbolSpec)
    smc: SmcConfig = field(default_factory=SmcConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    timeframe: str = "M15"

    htf: str = ""
    """Unité de temps supérieure donnant le biais, ex. "M15" ("" = désactivé).

    Quand elle est renseignée, la structure est lue sur cette unité de temps et
    les entrées sont cherchées sur `timeframe`. Les bougies supérieures sont
    reconstruites à partir des bougies courantes et ne sont transmises au
    moteur qu'une fois **terminées** : le biais accuse donc un retard réaliste.
    """

    htf_smc: SmcConfig = field(default_factory=SmcConfig)
    """Paramètres SMC propres à l'unité de temps supérieure."""

    strategy: str = "smc"
    """Nom de la stratégie à exécuter (voir smcbot.registry.STRATEGIES)."""

    strategy_params: dict = field(default_factory=dict)
    """Paramètres propres à la stratégie choisie, en clair dans la config."""

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
            filters=FilterConfig(**raw.get("filters", {})),
            timeframe=raw.get("timeframe", "M15"),
            htf=raw.get("htf", ""),
            htf_smc=SmcConfig(**raw.get("htf_smc", {})),
            strategy=raw.get("strategy", "smc"),
            strategy_params=dict(raw.get("strategy_params", {})),
        )


# --------------------------------------------------------------------- presets


def eurusd() -> SymbolSpec:
    """EURUSD 5 digits, compte en USD."""
    return SymbolSpec(
        name="EURUSD",
        digits=5,
        point=0.00001,
        contract_size=100_000.0,
        value_per_point_per_lot=1.0,
        spread_points=10.0,
        commission_per_lot=0.0,
    )


def xauusd() -> SymbolSpec:
    """XAUUSD (or) 2 digits, 100 onces par lot, compte en USD.

    1 point = 0,01 $ de variation ; 100 onces × 0,01 = 1 $ par point et par lot.
    Le spread par défaut (25 points, soit 0,25 $) correspond à un compte retail
    en pleine session ; il double facilement hors session et sur news.
    """
    return SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        contract_size=100.0,
        value_per_point_per_lot=1.0,
        min_lot=0.01,
        max_lot=50.0,
        lot_step=0.01,
        spread_points=25.0,
        commission_per_lot=0.0,
    )


def scalping_xauusd() -> BotConfig:
    """Configuration de scalping XAUUSD : entrées M1, biais M15.

    Les filtres sont volontairement stricts : sur l'or, le spread représente
    une part importante d'un stop serré, et la majorité des setups M1 ne
    méritent pas d'être pris.
    """
    return BotConfig(
        symbol=xauusd(),
        timeframe="M1",
        htf="M15",
        # Sur M1, la structure est bruitée : swings courts, zones périssables.
        smc=SmcConfig(
            swing_lookback=2,
            ob_lookback=8,
            ob_max_age=30,
            require_fvg=True,
            require_sweep=False,
        ),
        htf_smc=SmcConfig(swing_lookback=3, ob_lookback=12, ob_max_age=60),
        risk=RiskConfig(
            risk_pct=0.5,
            sl_buffer_points=15.0,
            tp_r=2.0,
            max_concurrent=1,
            max_daily_loss_pct=3.0,
            breakeven_at_r=1.0,
        ),
        filters=FilterConfig(
            sessions=["07:00-11:00", "13:00-17:00"],  # Londres et NY, en UTC
            weekdays=[0, 1, 2, 3, 4],
            max_spread_points=40.0,
            max_cost_ratio=0.30,
            # 100 points = 1,00 $ sur l'or. Avec 25 points de spread, les frais
            # pèsent alors 25 % du risque, sous le plafond de 30 % : les deux
            # filtres sont cohérents entre eux. Descendre ce plancher sans
            # baisser le spread rendrait le filtre de coût seul décisionnaire.
            min_stop_points=100.0,
            max_trades_per_day=6,
        ),
    )


def m15_xauusd() -> BotConfig:
    """XAUUSD en M15, biais H1 — la réponse à un signal mangé par les frais.

    Mesuré sur des données réelles, le schéma SMC en M1 sur l'or dégage environ
    +0,06 R par trade avant frais, pour un spread qui en coûte 0,098 : l'avantage
    est réel mais insuffisant. Le spread étant fixe, le seul levier est
    d'élargir le risque — donc de monter d'unité de temps.

    La ponction du spread sur l'espérance vaut à peu près le rapport
    frais / distance au stop : sur l'or, 24 points de spread contre 245 points
    de risque coûtaient 0,098 R par trade. Ramener cette ponction sous 0,05 R
    exige donc un risque d'au moins 480 points, et 800 pour descendre à 0,03 R —
    d'où les seuils ci-dessous, calculés et non ajustés après coup.

    Les autres paramètres reprennent le meilleur réglage validé en M1
    (tp_r = 3, swings = 3, breakeven à 1 R).

    Cette configuration est une **hypothèse dérivée d'une mesure**, pas un
    réglage validé : le signal M15 n'est pas le signal M1, et rien ne garantit
    que l'avantage se transporte. À vérifier avec `optimize --split`.
    """
    return BotConfig(
        symbol=xauusd(),
        timeframe="M15",
        htf="H1",
        smc=SmcConfig(
            swing_lookback=3,
            ob_lookback=10,
            ob_max_age=40,
            require_fvg=True,
        ),
        htf_smc=SmcConfig(swing_lookback=3, ob_lookback=12, ob_max_age=60),
        risk=RiskConfig(
            risk_pct=0.5,
            sl_buffer_points=60.0,
            tp_r=3.0,
            max_concurrent=1,
            max_daily_loss_pct=3.0,
            breakeven_at_r=1.0,
        ),
        filters=FilterConfig(
            sessions=["07:00-11:00", "13:00-17:00"],
            weekdays=[0, 1, 2, 3, 4],
            max_spread_points=40.0,
            # 5 % de frais au maximum, soit une ponction d'environ 0,05 R :
            # c'est tout l'intérêt de monter d'unité de temps, autant l'imposer.
            # Avec 24 points de spread ce plafond impose déjà un stop d'au moins
            # 480 points, et il s'ajuste seul si le spread change — d'où
            # l'absence de plancher écrit en dur, qui ne pourrait qu'être faux
            # sur un autre instrument. Mesuré sur XAUUSD M15 : amplitude médiane
            # 476 points, donc un stop naturel d'environ 536 avec la marge.
            max_cost_ratio=0.05,
            min_stop_points=0.0,
            max_trades_per_day=4,
        ),
    )


def swing_eurusd() -> BotConfig:
    """Configuration d'origine : EURUSD M15, sans filtre horaire."""
    return BotConfig(symbol=eurusd(), timeframe="M15")


PRESETS = {
    "eurusd-m15": swing_eurusd,
    "xauusd-m15": m15_xauusd,
    "xauusd-scalp": scalping_xauusd,
}
