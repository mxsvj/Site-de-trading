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
    init_mt5,
    Candle,
    Mt5Feed,
    ReplayFeed,
    download_mt5,
    load_csv,
    resample,
    save_csv,
    synthetic_series,
)
from .doctor import run_diagnostics
from .filters import TradeFilters
from .lab import MIN_TRADES as LAB_MIN
from .lab import Journal, evaluer, juger
from .registry import STRATEGIES
from .paper import PaperTrader, setup_logging
from .quality import inspect_series, shift_times
from .risk import granularite_lot, stop_max_praticable


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
            "--resample",
            help="agréger les bougies vers une unité de temps supérieure, "
            "ex. M15 — évite de réexporter depuis MT5 pour tester un autre "
            "horizon",
        )
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

    p_scan = sub.add_parser(
        "scan",
        help="classer des instruments par aptitude au scalping (aucune "
        "hypothèse consommée : ce n'est pas un test de stratégie)",
    )
    p_scan.add_argument(
        "--symbols",
        default="XAUUSD,EURUSD,GBPUSD,USDJPY,NAS100,GER40,US30,BTCUSD",
        help="symboles à comparer, séparés par des virgules",
    )
    p_scan.add_argument("--timeframe", default="M5")
    p_scan.add_argument("--bars", type=int, default=5000)
    p_scan.add_argument("--mt5-path", help="chemin de terminal64.exe à utiliser")

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
    p_opt.add_argument(
        "--grid-be", help="valeurs de breakeven_at_r à tester, ex. 0,1,1.5"
    )
    p_opt.add_argument(
        "--grid-spread",
        help="valeurs de spread à tester, ex. 0,12,24 — outil de mesure et non "
        "de réglage : inclure 0 sépare la valeur du signal du coût des frais",
    )
    p_opt.add_argument(
        "--split",
        type=float,
        default=0.6,
        help="part de l'historique servant à classer les réglages ; le reste "
        "sert de validation (défaut 0.6)",
    )
    p_opt.add_argument("--top", type=int, default=10, help="nombre de lignes affichées")

    p_lab = sub.add_parser(
        "lab", help="comparer plusieurs stratégies, correction du multi-test incluse"
    )
    add_common(p_lab)
    p_lab.add_argument(
        "--strategies",
        default=",".join(sorted(STRATEGIES)),
        help="stratégies à comparer, séparées par des virgules",
    )
    p_lab.add_argument(
        "--grid-tp", default="1.5,2,3", help="valeurs de tp_r à tester"
    )
    p_lab.add_argument("--split", type=float, default=0.6)
    p_lab.add_argument(
        "--journal",
        default="runtime/hypotheses.json",
        help="fichier où sont comptées les hypothèses testées, "
        "sessions précédentes incluses",
    )
    p_lab.add_argument(
        "--reset-journal",
        action="store_true",
        help="repartir de zéro (à n'utiliser qu'en changeant de jeu de données)",
    )

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
        if decalage:
            candles = shift_times(candles, decalage)
        return _agreger(candles, args, cfg)
    if getattr(args, "demo", False):
        # La série de démo suit l'échelle de prix et la cadence du symbole visé.
        return _agreger(synthetic_series(
            n=args.demo_bars,
            start=2650.0 if cfg.symbol.point >= 0.01 else 1.10000,
            point=cfg.symbol.point,
            seed=args.seed,
            timeframe_minutes=TIMEFRAMES.get(cfg.timeframe, 15),
        ), args, cfg)
    raise SystemExit("Précise une source de données : --csv FICHIER ou --demo")


def _agreger(candles, args, cfg: BotConfig):
    """Applique --resample : mêmes données, horizon plus large.

    Élargir l'unité de temps élargit mécaniquement les stops, donc réduit la
    part du risque absorbée par un spread fixe. C'est la réponse au cas où le
    signal a un avantage réel mais inférieur aux frais.
    """
    cible = getattr(args, "resample", None)
    if not cible:
        return candles

    minutes = TIMEFRAMES.get(cible)
    if minutes is None:
        raise SystemExit(f"Unité de temps inconnue : {cible}")

    agregees = resample(candles, minutes)
    print(
        f"Agrégation : {len(candles)} bougies → {len(agregees)} bougies {cible}"
    )
    cfg.timeframe = cible
    return agregees


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

    _fenetre_praticable(cfg, report)

    filtres = TradeFilters(cfg.filters, cfg.symbol)
    plancher = max(cfg.filters.min_stop_points, filtres.implied_min_stop())
    if plancher and report.median_range_points:
        origine = (
            "plancher explicite"
            if cfg.filters.min_stop_points >= filtres.implied_min_stop()
            else f"plafond de frais à {cfg.filters.max_cost_ratio:.0%}"
        )
        bougies = plancher / report.median_range_points
        print(
            f"\nStop minimal effectif : {plancher:.0f} points ({origine}), "
            f"soit {bougies:.1f} bougie(s) d'amplitude médiane."
        )
        if bougies < 1:
            print(
                "  → Sous une bougie médiane : la plupart des stops seront "
                "touchés par le simple bruit."
            )
        elif bougies > 2:
            print(
                "  → Plus de deux bougies médianes : beaucoup de setups seront "
                "écartés. Vérifie le nombre de trades avant de conclure."
            )

    return 0 if report.clean else 2


def cmd_doctor(args: argparse.Namespace) -> int:
    diagnostic = run_diagnostics(args.symbol, args.timeframe, args.mt5_path)
    print(diagnostic.to_text())
    return 0 if diagnostic.ok else 1


def _fenetre_praticable(cfg: BotConfig, report) -> None:
    """Quelles largeurs de stop ce compte peut-il réellement jouer ?

    Deux contraintes se referment l'une sur l'autre : les frais imposent un stop
    minimal, le lot minimal impose un stop maximal. Entre les deux se trouve la
    seule fenêtre exploitable — et sur un petit compte elle peut être vide.
    """
    filtres = TradeFilters(cfg.filters, cfg.symbol)
    plafond = stop_max_praticable(cfg.risk.initial_balance, cfg.risk, cfg.symbol)
    plancher = max(cfg.filters.min_stop_points, filtres.implied_min_stop())

    print(
        f"\n┌─ Fenêtre praticable ───────────────────────\n"
        f"│ Capital / risque    : {cfg.risk.initial_balance:,.0f} "
        f"× {cfg.risk.risk_pct:g} % = "
        f"{cfg.risk.initial_balance * cfg.risk.risk_pct / 100:,.2f} par trade\n"
        f"│ Stop maximal        : {plafond:,.0f} points "
        f"(au-delà, volume sous le lot minimal)\n"
        f"│ Stop minimal        : {plancher:,.0f} points"
        + (" (plafond de frais)" if plancher else " (aucune contrainte de frais)")
    )

    if report.median_range_points:
        print(
            f"│ Amplitude médiane   : {report.median_range_points:,.0f} points, "
            f"soit {plafond / report.median_range_points:.1f} bougie(s) au plafond"
        )
    print("└────────────────────────────────────────────")

    if plancher and plancher >= plafond:
        print(
            f"\n⚠ Fenêtre vide : les frais exigent au moins {plancher:.0f} points "
            f"de stop, le lot minimal en interdit plus de {plafond:.0f}.\n"
            "  Aucun trade ne peut satisfaire les deux. Il faut soit un capital "
            "plus important, soit un instrument moins cher,\n"
            "  soit accepter une part de frais plus élevée."
        )
        return

    largeur = plafond - plancher
    if plancher and largeur < plafond * 0.3:
        print(
            f"\n⚠ Fenêtre étroite : seuls les stops entre {plancher:.0f} et "
            f"{plafond:.0f} points sont jouables.\n"
            "  La plupart des setups tomberont d'un côté ou de l'autre — "
            "surveille le nombre de trades effectivement pris."
        )

    ecart = granularite_lot(
        cfg.risk.initial_balance, max(plancher, plafond / 2), cfg.risk, cfg.symbol
    )
    if ecart > 0.1:
        print(
            f"\n⚠ Arrondi du volume : jusqu'à {ecart:.0%} d'écart entre le risque "
            f"visé et le risque réel.\n"
            f"  Le pas de {cfg.symbol.lot_step:g} lot est grossier face aux volumes "
            "que ce capital permet."
        )


# Coût de transaction rapporté à l'amplitude d'une bougie, pour un stop de deux
# bougies. En deçà de 5 %, le scalping garde de la marge ; au-delà de 10 %, les
# frais mangent l'essentiel de ce qu'une stratégie peut espérer gagner.
SCALP_BON = 0.05
SCALP_LIMITE = 0.10


def cmd_scan(args: argparse.Namespace) -> int:
    """Classe des instruments par rapport frais / amplitude.

    C'est le seul chiffre qui décide si le scalping est jouable sur un
    instrument : le spread est fixe, l'amplitude d'une bougie fixe la taille
    naturelle d'un stop, et leur rapport donne la part du risque perdue d'avance.

    Aucune stratégie n'est testée ici — donc aucune hypothèse n'est consommée.
    """
    import statistics

    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            "Le paquet MetaTrader5 est requis pour `scan` (Windows uniquement)."
        ) from exc

    init_mt5(mt5, args.mt5_path)
    lignes = []
    try:
        for nom in [s.strip() for s in args.symbols.split(",") if s.strip()]:
            if not mt5.symbol_select(nom, True):
                print(f"  {nom:<10} introuvable chez ce courtier")
                continue
            spec = mt5.symbol_info(nom)
            tf = getattr(mt5, f"TIMEFRAME_{args.timeframe}", None)
            if spec is None or tf is None:
                continue
            rates = mt5.copy_rates_from_pos(nom, tf, 0, args.bars)
            if rates is None or len(rates) < 100:
                print(f"  {nom:<10} historique insuffisant")
                continue

            amplitudes = [
                (float(r["high"]) - float(r["low"])) / spec.point for r in rates
            ]
            mediane = statistics.median(amplitudes)
            if mediane <= 0:
                continue
            ratio = spec.spread / mediane
            lignes.append((nom, spec.spread, mediane, ratio))
            print(f"  {nom:<10} spread {spec.spread:>5} pts, amplitude médiane "
                  f"{mediane:>7.0f} pts")
    finally:
        mt5.shutdown()

    if not lignes:
        raise SystemExit("Aucun instrument exploitable.")

    lignes.sort(key=lambda l: l[3])
    print(
        f"\n{'instrument':<12}{'spread':>8}{'amplitude':>11}{'frais/stop 2 bougies':>22}"
        f"  aptitude"
    )
    print("-" * 70)
    for nom, spread, mediane, ratio in lignes:
        cout = ratio / 2  # un stop d'environ deux bougies
        if cout < SCALP_BON:
            verdict = "favorable"
        elif cout < SCALP_LIMITE:
            verdict = "jouable, sans marge"
        else:
            verdict = "à écarter"
        print(
            f"{nom:<12}{spread:>8.0f}{mediane:>11.0f}{cout:>21.1%}  {verdict}"
        )

    print(
        f"\nLecture : le spread est fixe, l'amplitude d'une bougie donne la taille\n"
        f"naturelle d'un stop. Leur rapport est la part du risque perdue avant même\n"
        f"que la stratégie n'ait raison ou tort. Sous {SCALP_BON:.0%} le scalping\n"
        f"garde de la marge ; au-delà de {SCALP_LIMITE:.0%} il faut un avantage\n"
        f"brut que presque aucun schéma simple n'atteint."
    )
    return 0


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


def _grille(texte: str, conv):
    return [conv(v) for v in texte.split(",") if v.strip()]


def cmd_optimize(args: argparse.Namespace) -> int:
    """Recherche sur grille, avec séparation apprentissage / validation.

    Le classement se fait sur la première partie de l'historique ; la seconde,
    jamais utilisée pour choisir, sert à mesurer ce que le réglage retenu vaut
    réellement. Sans cette séparation, une grille assez large finit toujours
    par produire un résultat flatteur et sans valeur.
    """
    cfg = make_config(args)
    candles = load_candles(args, cfg)

    tps = _grille(args.grid_tp, float)
    swings = _grille(args.grid_swing, int)
    bes = _grille(args.grid_be, float) if args.grid_be else [cfg.risk.breakeven_at_r]

    split = args.split
    if not 0.0 < split < 1.0:
        raise SystemExit("--split doit être strictement compris entre 0 et 1.")

    coupure = int(len(candles) * split)
    apprentissage = candles[:coupure]
    # La validation est précédée d'une préchauffe prise dans l'apprentissage :
    # sans elle, la stratégie démarrerait sans structure et sous-traderait.
    prechauffe = min(coupure, 2000)
    validation = candles[coupure - prechauffe :]

    if len(apprentissage) < 500 or len(validation) < 500:
        raise SystemExit(
            "Historique trop court pour être séparé. Utilise plus de bougies "
            "ou rapproche --split de 0.5."
        )

    print(
        f"Apprentissage : {len(apprentissage)} bougies "
        f"({apprentissage[0].time:%Y-%m-%d} → {apprentissage[-1].time:%Y-%m-%d})\n"
        f"Validation    : {len(validation) - prechauffe} bougies "
        f"({candles[coupure].time:%Y-%m-%d} → {candles[-1].time:%Y-%m-%d})"
    )

    # Le spread est le seul paramètre qu'on ne « règle » pas : le faire varier
    # sert à décomposer la perte entre valeur du signal et coût de transaction.
    spreads = _grille(args.grid_spread, float) if args.grid_spread else [None]

    combinaisons = list(itertools.product(tps, swings, bes, spreads))
    total = len(combinaisons)
    print(
        f"\n{total} réglage(s) à évaluer, soit {total * 2} backtests. "
        f"Compte une à trois minutes."
    )

    montre_spread = len(spreads) > 1
    # Faire varier le spread change aussi les filtres qui en dépendent : à
    # spread nul, le plafond de frais n'impose plus aucun stop minimal et la
    # population de trades explose. Pour que les lignes restent comparables, le
    # seuil est figé une fois pour toutes sur le spread réel.
    reference = TradeFilters(cfg.filters, cfg.symbol)
    plancher_fige = max(cfg.filters.min_stop_points, reference.implied_min_stop())
    if montre_spread and plancher_fige:
        print(
            f"Filtres figés sur le spread réel : stop minimal {plancher_fige:.0f} "
            f"points pour toutes les lignes."
        )

    lignes = []
    for numero, (tp, swing, be, spread) in enumerate(combinaisons, 1):
        essai = make_config(args)
        essai.risk.tp_r = tp
        essai.risk.breakeven_at_r = be
        essai.smc.swing_lookback = swing
        if spread is not None:
            essai.symbol.spread_points = spread
            # Seul le coût d'exécution varie ; l'admissibilité des setups reste
            # celle du spread réel, sans quoi on comparerait deux stratégies
            # différentes au lieu de mesurer un coût.
            essai.filters.max_cost_ratio = 0.0
            essai.filters.min_stop_points = plancher_fige

        etiquette = f"tp_R={tp:g} swing={swing} BE={be:g}"
        if montre_spread:
            etiquette += f" spread={spread:g}"
        # Sur une grille large la commande tourne longtemps : sans retour à
        # l'écran, l'utilisateur ne sait pas si elle avance ou si elle a planté.
        print(f"  [{numero}/{total}] {etiquette} ...", end="", flush=True)

        dedans = run_backtest(apprentissage, essai).report
        dehors = run_backtest(validation, essai, warmup=prechauffe).report
        lignes.append((tp, swing, be, spread, dedans, dehors))
        print(
            f" {dedans.trades} trades, {dedans.expectancy_r:+.3f} R "
            f"→ validation {dehors.expectancy_r:+.3f} R"
        )

    lignes.sort(key=lambda r: r[4].expectancy_r, reverse=True)

    tete_spread = f"{'spread':>7} " if montre_spread else ""
    largeur = 18 + (8 if montre_spread else 0)
    print(
        f"\n{'tp_R':>5} {'swing':>6} {'BE':>5} {tete_spread}│ "
        f"{'trades':>7} {'esp.R':>8} {'PF':>6} │ "
        f"{'trades':>7} {'esp.R':>8} {'PF':>6} {'perf%':>8}"
    )
    print(f"{'':>{largeur}} │ {'— apprentissage —':^23} │ {'— validation —':^32}")
    print("-" * (82 + (8 if montre_spread else 0)))
    for tp, swing, be, spread, dedans, dehors in lignes[: args.top]:
        colonne = f"{spread:>7g} " if montre_spread else ""
        print(
            f"{tp:>5.1f} {swing:>6d} {be:>5.1f} {colonne}│ "
            f"{dedans.trades:>7d} {dedans.expectancy_r:>+8.3f} "
            f"{_pf(dedans):>6} │ "
            f"{dehors.trades:>7d} {dehors.expectancy_r:>+8.3f} "
            f"{_pf(dehors):>6} {dehors.return_pct:>+8.2f}"
        )

    if montre_spread:
        _decompose_cout(lignes)
    _verdict(lignes)
    return 0


def _decompose_cout(lignes: list) -> None:
    """Sépare la valeur brute du signal du coût de transaction.

    Rejouer à spread nul n'est pas un scénario tradable : c'est un instrument
    de mesure. Si l'espérance reste négative sans frais, le signal lui-même ne
    vaut rien ; si elle devient positive, le schéma fonctionne mais ne survit
    pas à ses coûts.

    La comparaison ne vaut qu'à **réglage identique** — même tp, même swing,
    même breakeven, seul le spread change. Confronter le meilleur réglage d'un
    groupe au meilleur d'un autre mélange deux effets et produit des écarts
    dénués de sens, jusqu'à des coûts négatifs, ce qui n'existe pas.
    """
    paires = []
    for tp, swing, be, spread, dedans, _ in lignes:
        if spread != 0:
            continue
        for tp2, swing2, be2, spread2, dedans2, _ in lignes:
            if (tp2, swing2, be2) == (tp, swing, be) and spread2 not in (None, 0):
                paires.append((dedans, dedans2))
    if not paires:
        return

    print("\n── Décomposition ──────────────────────────────────")
    exploitables = [
        (sans, avec)
        for sans, avec in paires
        if sans.trades >= MIN_TRADES and avec.trades >= MIN_TRADES
    ]
    if not exploitables:
        print(f"Non concluante : moins de {MIN_TRADES} trades par réglage.")
        return

    couts = sorted(sans.expectancy_r - avec.expectancy_r for sans, avec in exploitables)
    cout_median = couts[len(couts) // 2]
    brut = max(sans.expectancy_r for sans, _ in exploitables)

    print(f"Meilleure espérance brute (spread nul) : {brut:+.3f} R")
    print(
        f"Coût de transaction, médiane sur {len(exploitables)} réglages "
        f"appariés : {cout_median:+.3f} R par trade"
    )

    if cout_median < 0:
        print(
            "  ⚠ Un coût négatif est impossible : les deux séries ne portent pas "
            "sur les mêmes trades.\n"
            "  Résultat inexploitable — vérifie que les filtres ne dépendent pas "
            "du spread."
        )
        return

    if brut <= 0.02:
        print(
            "Le signal ne vaut rien en lui-même : même sans payer un centime de\n"
            "frais, il ne gagne pas. Le spread n'est pas le coupable, il n'a\n"
            "fait qu'aggraver une absence d'avantage. Changer d'instrument ou\n"
            "de timeframe ne sauvera pas ce schéma — il faut changer de schéma."
        )
    elif cout_median >= brut:
        print(
            f"Le signal a un avantage réel ({brut:+.3f} R) mais les frais le\n"
            f"dépassent. Ce n'est pas un problème de réglage : il faut des stops\n"
            f"plus larges, donc un timeframe supérieur, pour que le spread pèse\n"
            f"une part plus faible du risque."
        )
    else:
        print(
            f"L'avantage brut ({brut:+.3f} R) dépasse les frais "
            f"({cout_median:.3f} R) : la stratégie survit à ses coûts."
        )


# En deçà, aucune statistique n'est exploitable : l'erreur d'échantillonnage
# dépasse largement les écarts qu'on prétendrait mesurer. Une espérance tirée
# de dix trades peut valoir n'importe quoi.
MIN_TRADES = 30


def _echantillon_suffisant(dedans, dehors) -> bool:
    return dedans.trades >= MIN_TRADES and dehors.trades >= MIN_TRADES


def _refus_echantillon(dedans, dehors) -> None:
    print(
        f"Échantillon insuffisant : {dedans.trades} trades en apprentissage et "
        f"{dehors.trades} en validation, pour un minimum de {MIN_TRADES}.\n"
        "Aucune conclusion n'est tirée — sur si peu de trades, l'espérance et le "
        "profit factor sont dominés par le hasard, et\n"
        "les commenter reviendrait à raconter du bruit.\n\n"
        "Deux causes possibles, à traiter dans cet ordre :\n"
        "  1. Historique trop court pour cette unité de temps. Télécharge "
        "directement l'unité visée depuis MT5 plutôt que d'agréger :\n"
        "     50 000 bougies M15 couvrent environ 18 mois, contre 7 semaines "
        "pour 50 000 bougies M1.\n"
        "  2. Filtres trop stricts. Lance `check` sur les mêmes données pour "
        "comparer min_stop_points à l'amplitude médiane réelle."
    )


def _pf(rapport) -> str:
    valeur = rapport.profit_factor
    return "∞" if valeur == float("inf") else f"{valeur:.2f}"


def _verdict(lignes: list) -> None:
    """Dit franchement ce que vaut le meilleur réglage hors échantillon."""
    *_, dedans, dehors = lignes[0]
    print()

    if not _echantillon_suffisant(dedans, dehors):
        _refus_echantillon(dedans, dehors)
        return

    if dedans.expectancy_r <= 0:
        print(
            "Aucun réglage de la grille n'est rentable, même sur la période qui a "
            "servi à les classer. Ce n'est pas un problème de réglage : la\n"
            "stratégie n'a pas d'avantage sur cet instrument. Changer les "
            "paramètres jusqu'à trouver un chiffre positif reviendrait à\n"
            "sélectionner du bruit."
        )
        return

    if dehors.expectancy_r <= 0:
        print(
            f"Le meilleur réglage en apprentissage ({dedans.expectancy_r:+.3f} R) "
            f"perd en validation ({dehors.expectancy_r:+.3f} R).\n"
            "C'est la signature du surapprentissage : le réglage a mémorisé la "
            "période de test, il n'a rien appris de généralisable.\n"
            "Ne l'utilise pas."
        )
        return

    positifs = sum(1 for *_, d in lignes if d.expectancy_r > 0)
    print(
        f"Le meilleur réglage tient en validation "
        f"({dedans.expectancy_r:+.3f} R → {dehors.expectancy_r:+.3f} R), et "
        f"{positifs}/{len(lignes)} réglages y sont positifs.\n"
        "C'est encourageant, sans être une preuve : une seule période de "
        "validation, sur un seul instrument, reste un échantillon étroit.\n"
        "Étape suivante : un compte démo en temps réel, pendant plusieurs mois."
    )


def cmd_lab(args: argparse.Namespace) -> int:
    cfg = make_config(args)
    candles = load_candles(args, cfg)
    if len(candles) < 2000:
        raise SystemExit("Historique trop court pour un banc d'essai.")

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    inconnues = [s for s in strategies if s not in STRATEGIES]
    if inconnues:
        raise SystemExit(
            f"Stratégie(s) inconnue(s) : {', '.join(inconnues)}. "
            f"Disponibles : {', '.join(sorted(STRATEGIES))}"
        )

    tps = _grille(args.grid_tp, float)
    candidates = [
        (nom, {"tp_r": tp}, f"{nom} tp={tp:g}")
        for nom in strategies
        for tp in tps
    ]

    chemin = Path(args.journal) if args.journal else None
    if chemin and args.reset_journal and chemin.exists():
        chemin.unlink()
    journal = Journal(chemin)

    print(
        f"{len(candidates)} candidates à évaluer sur {len(candles)} bougies.\n"
        f"Hypothèses déjà testées lors des sessions précédentes : {journal.total}"
    )

    def avancement(numero: int, total: int, essai) -> None:
        print(
            f"  [{numero}/{total}] {essai.label:<24} "
            f"{essai.dedans.trades:>4} trades, {essai.dedans.expectancy_r:+.3f} R "
            f"→ validation {essai.dehors.expectancy_r:+.3f} R"
        )

    essais = evaluer(candidates, candles, cfg, split=args.split,
                     progression=avancement)

    print(
        f"\n{'stratégie':<24} │ {'trades':>7} {'esp.R':>8} │ "
        f"{'trades':>7} {'esp.R':>8} {'t':>6} {'perf%':>8}"
    )
    print(f"{'':<24} │ {'— apprentissage —':^17} │ {'— validation —':^32}")
    print("-" * 82)
    for essai in sorted(essais, key=lambda e: e.dedans.expectancy_r, reverse=True):
        marque = " " if essai.exploitable else "!"
        print(
            f"{marque}{essai.label:<23} │ "
            f"{essai.dedans.trades:>7} {essai.dedans.expectancy_r:>+8.3f} │ "
            f"{essai.dehors.trades:>7} {essai.dehors.expectancy_r:>+8.3f} "
            f"{essai.t_validation:>6.2f} {essai.dehors.return_pct:>+8.2f}"
        )
    if any(not e.exploitable for e in essais):
        print(f"\n! = moins de {LAB_MIN} trades d'un côté : ligne non exploitable.")

    print()
    print(juger(essais, journal, detail=args.strategies).to_text())
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
    "lab": cmd_lab,
    "download": cmd_download,
    "scan": cmd_scan,
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
