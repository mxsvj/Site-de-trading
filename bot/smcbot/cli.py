"""Interface en ligne de commande du bot SMC."""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path
from typing import Sequence

from .backtest import run_backtest
from .config import BotConfig
from .data import (
    Candle,
    Mt5Feed,
    ReplayFeed,
    download_mt5,
    load_csv,
    save_csv,
    synthetic_series,
)
from .paper import PaperTrader, setup_logging


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

        cf = p.add_argument_group("configuration")
        cf.add_argument("--config", help="fichier JSON de configuration")
        cf.add_argument("--symbol", help="nom du symbole (ex. EURUSD)")
        cf.add_argument("--timeframe", default=None, help="unité de temps (ex. M15)")
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

    p_dl = sub.add_parser("download", help="exporter un historique MT5 en CSV")
    p_dl.add_argument("--symbol", required=True)
    p_dl.add_argument("--timeframe", default="M15")
    p_dl.add_argument("--bars", type=int, default=5000)
    p_dl.add_argument("--out", required=True)

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

    return parser


def make_config(args: argparse.Namespace) -> BotConfig:
    """Construit la configuration : fichier JSON puis surcharges CLI."""
    cfg = BotConfig.from_json(args.config) if getattr(args, "config", None) else BotConfig()

    if getattr(args, "symbol", None):
        cfg.symbol.name = args.symbol
    if getattr(args, "timeframe", None):
        cfg.timeframe = args.timeframe
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

    return cfg


def load_candles(args: argparse.Namespace, cfg: BotConfig) -> list[Candle]:
    """Charge les bougies depuis un CSV ou génère la série de démo."""
    if getattr(args, "csv", None):
        candles = load_csv(args.csv)
        if not candles:
            raise SystemExit(f"Aucune bougie lue dans {args.csv}")
        return candles
    if getattr(args, "demo", False):
        return synthetic_series(
            n=args.demo_bars, point=cfg.symbol.point, seed=args.seed
        )
    raise SystemExit("Précise une source de données : --csv FICHIER ou --demo")


# ------------------------------------------------------------------ commandes


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = make_config(args)
    candles = load_candles(args, cfg)
    result = run_backtest(candles, cfg)

    print(
        f"{cfg.symbol.name} {cfg.timeframe} — {result.candles} bougies "
        f"du {candles[0].time:%Y-%m-%d} au {candles[-1].time:%Y-%m-%d}"
    )
    print(f"Signaux générés : {result.signals}")
    print(result.report.to_text())

    if result.rejected:
        print(f"\n{len(result.rejected)} signal(aux) non exécuté(s). Exemples :")
        for line in result.rejected[:3]:
            print(f"  - {line}")

    if args.list_trades:
        print("\nDétail des trades :")
        for i, t in enumerate(result.trades, 1):
            sens = "achat " if t.direction == "bullish" else "vente "
            print(
                f"{i:4d}. {t.open_time:%Y-%m-%d %H:%M} {sens}"
                f"{t.lots:>5g}l @ {t.entry:.5f} → {t.exit:.5f} "
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
        feed = Mt5Feed(cfg.symbol.name, cfg.timeframe)
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


def cmd_download(args: argparse.Namespace) -> int:
    candles = download_mt5(args.symbol, args.timeframe, args.bars)
    save_csv(candles, args.out)
    print(
        f"{len(candles)} bougies {args.symbol} {args.timeframe} "
        f"écrites dans {args.out}"
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
    BotConfig().to_json(args.out)
    print(f"Configuration par défaut écrite dans {args.out}")
    return 0


COMMANDS = {
    "backtest": cmd_backtest,
    "paper": cmd_paper,
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
