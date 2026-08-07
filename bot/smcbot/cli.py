"""Interface en ligne de commande du bot SMC."""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import fields
from pathlib import Path
from typing import Sequence

from .backtest import run_backtest
from .config import PRESETS, BotConfig, SymbolSpec, eurusd, xauusd
from .data import (
    TIMEFRAMES,
    Candle,
    Mt5Feed,
    ReplayFeed,
    download_mt5,
    load_csv,
    save_csv,
    synthetic_series,
)
from .doctor import run_diagnostics
from .paper import PaperTrader, setup_logging
from .quality import inspect_series, shift_times


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smcbot",
        description="Bot SMC pour MetaTrader 5 — backtest et paper trading.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ---------------------------------------------------------- paramètres
    def add_common(p: argparse.ArgumentParser) -> None:
        src = p.add_argument_group("données")
        src.add_argument("--csv", help="fichier de bougies OHLC")
        src.add_argument(
            "--demo",
            action="store_true",
            help="utiliser une série synthétique (aucune donnée requise)",
        )
        src.add_argument(
            "--demo-bars", type=int, default=3000, help="taille de la série de démo"
        )
        src.add_argument("--seed", type=int, default=7, help="graine de la démo")
        src.add_argument(
            "--tz-shift",
            type=float,
            default=0.0,
            help="décalage horaire à appliquer aux bougies, en heures "
            "(ex. -3 pour ramener un serveur UTC+3 en UTC)",
        )

        cf = p.add_argument_group("configuration")
        cf.add_argument("--config", help="fichier JSON de configuration")
        cf.add_argument(
            "--preset",
            choices=sorted(PRESETS),
            help="configuration prête à l'emploi (base, surchargeable ensuite)",
        )
        cf.add_argument("--symbol", help="nom du symbole (ex. EURUSD)")
        cf.add_argument(
            "--symbol-preset",
            choices=("eurusd", "xauusd"),
            help="caractéristiques du contrat (point, lot, spread typique)",
        )
        cf.add_argument(
            "--symbol-spec",
            help="fichier JSON de spécification produit par mt5/ExportBars.mq5",
        )
        cf.add_argument("--timeframe", default=None, help="unité de temps (ex. M15)")
        cf.add_argument(
            "--htf",
            help="unité de temps du biais, ex. M15 (vide = mono-timeframe)",
        )
        cf.add_argument("--balance", type=float, help="capital de départ")
        cf.add_argument("--risk", type=float, help="risque par trade en %%")
        cf.add_argument("--tp-r", type=float, help="take profit en multiple de R")
        cf.add_argument("--swing", type=int, help="profondeur de détection des swings")
        cf.add_argument("--spread", type=float, help="spread en points")
        cf.add_argument("--commission", type=float, help="commission par lot (A/R)")
        cf.add_argument("--sl-buffer", type=float, help="marge du stop en points")
        cf.add_argument("--breakeven", type=float, help="passage à BE à N R (0=off)")
        cf.add_argument(
            "--no-fvg",
            action="store_true",
            help="ne pas exiger de FVG dans la jambe impulsive",
        )
        cf.add_argument(
            "--sweep",
            action="store_true",
            help="exiger une prise de liquidité avant la cassure",
        )
        cf.add_argument(
            "--equilibrium",
            action="store_true",
            help="entrer à 50 %% de l'order block",
        )
        cf.add_argument(
            "--htf-zone",
            action="store_true",
            help="n'entrer que dans une zone de l'unité de temps supérieure",
        )

        fl = p.add_argument_group("filtres (déterminants en scalping)")
        fl.add_argument(
            "--sessions",
            help='plages UTC autorisées, ex. "07:00-11:00,13:00-17:00"',
        )
        fl.add_argument(
            "--no-sessions", action="store_true", help="supprimer le filtre horaire"
        )
        fl.add_argument("--max-spread", type=float, help="spread maximal en points")
        fl.add_argument(
            "--max-cost",
            type=float,
            help="part maximale du risque absorbée par les frais (0.30 = 30 %%)",
        )
        fl.add_argument("--min-stop", type=float, help="distance minimale du stop")
        fl.add_argument("--max-trades-day", type=int, help="plafond de trades par jour")

    p_bt = sub.add_parser("backtest", help="rejouer la stratégie sur un historique")
    add_common(p_bt)
    p_bt.add_argument("--out-trades", help="export CSV du journal des trades")
    p_bt.add_argument("--out-equity", help="export CSV de la courbe de capital")
    p_bt.add_argument(
        "--list-trades", action="store_true", help="afficher le détail des trades"
    )

    p_paper = sub.add_parser("paper", help="trading simulé en continu")
    add_common(p_paper)
    p_paper.add_argument(
        "--mt5", action="store_true", help="lire les bougies en direct depuis MT5"
    )
    p_paper.add_argument("--mt5-path", help="chemin de terminal64.exe à utiliser")
    p_paper.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="secondes entre deux interrogations du flux (0 = aussi vite que possible)",
    )
    p_paper.add_argument("--max-bars", type=int, help="nombre de bougies à traiter")
    p_paper.add_argument(
        "--state", default="runtime/paper_state.json", help="fichier d'état"
    )
    p_paper.add_argument("--log", default="runtime/paper.log", help="fichier de log")
    p_paper.add_argument(
        "--stop-file",
        default="runtime/STOP",
        help="créer ce fichier arrête proprement la boucle",
    )
    p_paper.add_argument(
        "--warmup", type=int, default=300, help="bougies d'historique de préchauffe"
    )

    p_check = sub.add_parser(
        "check", help="contrôler la qualité d'un historique avant de le backtester"
    )
    add_common(p_check)

    p_doc = sub.add_parser(
        "doctor", help="diagnostiquer l'installation et la connexion à MT5"
    )
    p_doc.add_argument("--symbol", default="XAUUSD")
    p_doc.add_argument("--timeframe", default="M1")
    p_doc.add_argument("--mt5-path", help="chemin de terminal64.exe à utiliser")

    p_dl = sub.add_parser("download", help="exporter un historique MT5 en CSV")
    p_dl.add_argument("--symbol", required=True)
    p_dl.add_argument("--timeframe", default="M15")
    p_dl.add_argument("--bars", type=int, default=5000)
    p_dl.add_argument("--out", required=True)
    p_dl.add_argument("--mt5-path", help="chemin de terminal64.exe à utiliser")

    p_demo = sub.add_parser("demo-data", help="générer un CSV synthétique")
    p_demo.add_argument("--out", required=True)
    p_demo.add_argument("--bars", type=int, default=3000)
    p_demo.add_argument("--seed", type=int, default=7)

    p_opt = sub.add_parser("optimize", help="petite recherche sur grille")
    add_common(p_opt)
    p_opt.add_argument(
        "--grid-tp", default="1.5,2,3", help="valeurs de tp_r à tester"
    )
    p_opt.add_argument(
        "--grid-swing", default="2,3,4", help="valeurs de swing_lookback à tester"
    )
    p_opt.add_argument("--top", type=int, default=10, help="nombre de lignes affichées")

    p_cfg = sub.add_parser("init-config", help="écrire une configuration par défaut")
    p_cfg.add_argument("--out", default="config.json")
    p_cfg.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default="eurusd-m15",
        help="modèle de départ",
    )

    return parser


def load_symbol_spec(path: str) -> SymbolSpec:
    """Charge la spécification écrite par mt5/ExportBars.mq5.

    Les champs inconnus sont ignorés plutôt que de faire échouer la commande :
    une version ultérieure du script peut en ajouter.
    """
    try:
        brut = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Spécification illisible ({path}) : {exc}") from exc

    connus = {f.name for f in fields(SymbolSpec)}
    inconnus = sorted(set(brut) - connus)
    if inconnus:
        print(f"Champs ignorés dans {path} : {', '.join(inconnus)}")
    return SymbolSpec(**{k: v for k, v in brut.items() if k in connus})


def make_config(args: argparse.Namespace) -> BotConfig:
    """Construit la configuration : preset, puis fichier JSON, puis CLI."""
    if getattr(args, "config", None):
        cfg = BotConfig.from_json(args.config)
    elif getattr(args, "preset", None):
        cfg = PRESETS[args.preset]()
    else:
        cfg = BotConfig()

    if getattr(args, "symbol_preset", None):
        cfg.symbol = {"eurusd": eurusd, "xauusd": xauusd}[args.symbol_preset]()
    if getattr(args, "symbol_spec", None):
        cfg.symbol = load_symbol_spec(args.symbol_spec)
    if getattr(args, "symbol", None):
        cfg.symbol.name = args.symbol
    if getattr(args, "timeframe", None):
        cfg.timeframe = args.timeframe
    if getattr(args, "htf", None) is not None:
        cfg.htf = args.htf
    if getattr(args, "balance", None) is not None:
        cfg.risk.initial_balance = args.balance
    if getattr(args, "risk", None) is not None:
        cfg.risk.risk_pct = args.risk
    if getattr(args, "tp_r", None) is not None:
        cfg.risk.tp_r = args.tp_r
    if getattr(args, "sl_buffer", None) is not None:
        cfg.risk.sl_buffer_points = args.sl_buffer
    if getattr(args, "breakeven", None) is not None:
        cfg.risk.breakeven_at_r = args.breakeven
    if getattr(args, "swing", None) is not None:
        cfg.smc.swing_lookback = args.swing
    if getattr(args, "spread", None) is not None:
        cfg.symbol.spread_points = args.spread
    if getattr(args, "commission", None) is not None:
        cfg.symbol.commission_per_lot = args.commission
    if getattr(args, "no_fvg", False):
        cfg.smc.require_fvg = False
    if getattr(args, "sweep", False):
        cfg.smc.require_sweep = True
    if getattr(args, "equilibrium", False):
        cfg.smc.entry_at_equilibrium = True
    if getattr(args, "htf_zone", False):
        cfg.smc.require_htf_zone = True

    if getattr(args, "no_sessions", False):
        cfg.filters.sessions = []
    elif getattr(args, "sessions", None):
        cfg.filters.sessions = [s.strip() for s in args.sessions.split(",") if s.strip()]
    if getattr(args, "max_spread", None) is not None:
        cfg.filters.max_spread_points = args.max_spread
    if getattr(args, "max_cost", None) is not None:
        cfg.filters.max_cost_ratio = args.max_cost
    if getattr(args, "min_stop", None) is not None:
        cfg.filters.min_stop_points = args.min_stop
    if getattr(args, "max_trades_day", None) is not None:
        cfg.filters.max_trades_per_day = args.max_trades_day

    return cfg


def _csv_voisins(chemin: Path) -> str:
    """Liste les CSV du dossier visé, pour rattraper une faute de frappe."""
    dossier = chemin.parent if str(chemin.parent) else Path(".")
    try:
        noms = sorted(p.name for p in dossier.glob("*.csv"))
    except OSError:
        return "(dossier illisible)"
    return ", ".join(noms) if noms else "aucun"


def load_candles(args: argparse.Namespace, cfg: BotConfig) -> list[Candle]:
    """Charge les bougies depuis un CSV ou génère la série de démo."""
    if getattr(args, "csv", None):
        chemin = Path(args.csv)
        if not chemin.exists():
            # Cas le plus fréquent : l'export qui devait produire ce fichier a
            # échoué. Le dire, plutôt que de laisser remonter une trace Python.
            raise SystemExit(
                f"Fichier introuvable : {chemin}\n"
                f"Si tu viens de lancer `download`, c'est cette commande-là qui a "
                f"échoué — relance-la et lis son message d'erreur.\n"
                f"Fichiers CSV présents ici : {_csv_voisins(chemin)}"
            )
        candles = load_csv(chemin)
        if not candles:
            raise SystemExit(f"Aucune bougie lue dans {chemin}")
        decalage = getattr(args, "tz_shift", 0.0)
        return shift_times(candles, decalage) if decalage else candles
    if getattr(args, "demo", False):
        # La série de démo suit l'échelle de prix et la cadence du symbole visé.
        return synthetic_series(
            n=args.demo_bars,
            start=2650.0 if cfg.symbol.point >= 0.01 else 1.10000,
            point=cfg.symbol.point,
            seed=args.seed,
            timeframe_minutes=TIMEFRAMES.get(cfg.timeframe, 15),
        )
    raise SystemExit("Précise une source de données : --csv FICHIER ou --demo")


# ------------------------------------------------------------------ commandes


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = make_config(args)
    candles = load_candles(args, cfg)
    result = run_backtest(candles, cfg)

    entete = f"{cfg.symbol.name} {cfg.timeframe}"
    if cfg.htf:
        entete += f" (biais {cfg.htf})"
    print(
        f"{entete} — {result.candles} bougies "
        f"du {candles[0].time:%Y-%m-%d} au {candles[-1].time:%Y-%m-%d}"
    )
    if cfg.filters.sessions:
        print(f"Sessions UTC : {', '.join(cfg.filters.sessions)}")
    print(f"Signaux générés : {result.signals}")
    print(result.report.to_text())

    if result.skipped:
        total = sum(result.skipped.values())
        print(f"\nSetups écartés par les filtres : {total}")
        for motif, nombre in sorted(
            result.skipped.items(), key=lambda kv: kv[1], reverse=True
        ):
            print(f"  {nombre:>5} × {motif}")

    if args.list_trades:
        px = f"%.{cfg.symbol.digits}f"
        print("\nDétail des trades :")
        for i, t in enumerate(result.trades, 1):
            sens = "achat " if t.direction == "bullish" else "vente "
            print(
                f"{i:4d}. {t.open_time:%Y-%m-%d %H:%M} {sens}"
                f"{t.lots:>5g}l @ {px % t.entry} → {px % t.exit} "
                f"{t.exit_reason:<3} {t.pnl:+9.2f} ({t.r:+.2f}R)  {t.reason}"
            )

    if args.out_trades:
        result.save_trades(args.out_trades)
        print(f"\nJournal des trades écrit dans {args.out_trades}")
    if args.out_equity:
        result.save_equity(args.out_equity)
        print(f"Courbe de capital écrite dans {args.out_equity}")

    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    cfg = make_config(args)
    setup_logging(args.log)

    trader = PaperTrader(cfg, state_path=args.state)

    if args.mt5:
        feed = Mt5Feed(cfg.symbol.name, cfg.timeframe, args.mt5_path)
        trader.warmup(feed.history(args.warmup))
        interval = args.interval if args.interval > 0 else 30.0
    else:
        candles = load_candles(args, cfg)
        warmup = min(args.warmup, max(0, len(candles) - 1))
        feed = ReplayFeed(candles, warmup=warmup)
        trader.warmup(feed.warmup_candles)
        interval = args.interval

    stop_file = Path(args.stop_file) if args.stop_file else None
    if stop_file is not None and stop_file.exists():
        stop_file.unlink()  # ne pas hériter d'un arrêt précédent

    report = trader.run(
        feed,
        poll_seconds=interval,
        max_bars=args.max_bars,
        stop_file=stop_file,
    )
    print(report.to_text())
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    cfg = make_config(args)
    candles = load_candles(args, cfg)
    report = inspect_series(candles, cfg.symbol)
    print(report.to_text())

    if report.inferred_minutes and cfg.timeframe in TIMEFRAMES:
        attendu = TIMEFRAMES[cfg.timeframe]
        if attendu != report.inferred_minutes:
            print(
                f"\n⚠ Tu annonces {cfg.timeframe} ({attendu} min) mais le fichier "
                f"contient des bougies de {report.inferred_minutes} min."
            )

    if cfg.filters.min_stop_points and report.median_range_points:
        bougies = cfg.filters.min_stop_points / report.median_range_points
        print(
            f"\nLe plancher de stop ({cfg.filters.min_stop_points:.0f} pts) vaut "
            f"{bougies:.1f} bougie(s) d'amplitude médiane."
        )
        if bougies < 1:
            print(
                "  → Très serré au regard de la volatilité : la plupart des stops "
                "seront touchés par le bruit."
            )

    return 0 if report.clean else 2


def cmd_doctor(args: argparse.Namespace) -> int:
    diagnostic = run_diagnostics(args.symbol, args.timeframe, args.mt5_path)
    print(diagnostic.to_text())
    return 0 if diagnostic.ok else 1


def cmd_download(args: argparse.Namespace) -> int:
    candles = download_mt5(args.symbol, args.timeframe, args.bars, args.mt5_path)

    sortie = Path(args.out)
    if sortie.parent and not sortie.parent.exists():
        sortie.parent.mkdir(parents=True, exist_ok=True)
    save_csv(candles, sortie)

    print(
        f"{len(candles)} bougies {args.symbol} {args.timeframe} écrites dans "
        f"{sortie.resolve()}"
    )
    if len(candles) < args.bars:
        print(
            f"Note : {args.bars} demandées, {len(candles)} disponibles. Fais "
            f"défiler le graphique vers la gauche dans MT5 pour en charger plus."
        )
    print(
        "Les horodatages sont à l'heure du serveur. Lance `check` pour trouver "
        "le décalage à appliquer."
    )
    return 0


def cmd_demo_data(args: argparse.Namespace) -> int:
    candles = synthetic_series(n=args.bars, seed=args.seed)
    save_csv(candles, args.out)
    print(f"{len(candles)} bougies synthétiques écrites dans {args.out}")
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    cfg = make_config(args)
    candles = load_candles(args, cfg)

    tps = [float(v) for v in args.grid_tp.split(",") if v.strip()]
    swings = [int(v) for v in args.grid_swing.split(",") if v.strip()]

    rows = []
    for tp, swing in itertools.product(tps, swings):
        trial = make_config(args)
        trial.risk.tp_r = tp
        trial.smc.swing_lookback = swing
        result = run_backtest(candles, trial)
        rows.append((tp, swing, result.report))

    rows.sort(key=lambda r: r[2].expectancy_r, reverse=True)

    print(f"{'tp_R':>6} {'swing':>6} {'trades':>7} {'win%':>7} {'PF':>7} "
          f"{'esp.R':>8} {'perf%':>8} {'DD%':>7}")
    print("-" * 60)
    for tp, swing, rep in rows[: args.top]:
        pf = "∞" if rep.profit_factor == float("inf") else f"{rep.profit_factor:.2f}"
        print(
            f"{tp:>6.2f} {swing:>6d} {rep.trades:>7d} {rep.winrate:>7.1f} "
            f"{pf:>7} {rep.expectancy_r:>+8.3f} {rep.return_pct:>+8.2f} "
            f"{rep.max_drawdown_pct:>7.2f}"
        )
    print(
        "\nAttention : plus la grille est large, plus le meilleur résultat risque "
        "d'être du surapprentissage. Valide toujours sur une période non testée."
    )
    return 0


def cmd_init_config(args: argparse.Namespace) -> int:
    PRESETS[args.preset]().to_json(args.out)
    print(f"Configuration « {args.preset} » écrite dans {args.out}")
    return 0


COMMANDS = {
    "backtest": cmd_backtest,
    "check": cmd_check,
    "paper": cmd_paper,
    "doctor": cmd_doctor,
    "download": cmd_download,
    "demo-data": cmd_demo_data,
    "optimize": cmd_optimize,
    "init-config": cmd_init_config,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (RuntimeError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # Fichier absent, illisible, disque plein : un message, pas une trace.
        print(f"Erreur d'accès au fichier : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
