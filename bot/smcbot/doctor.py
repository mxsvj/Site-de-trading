"""Diagnostic de l'installation : Python, paquet MT5, terminal, symbole.

Objectif : remplacer un aller-retour de messages d'erreur par une seule commande
qui dit exactement où la chaîne casse.

Rien de confidentiel n'est affiché — ni numéro de compte, ni solde, ni mot de
passe. Seulement ce qui sert au diagnostic, pour que la sortie puisse être
collée telle quelle dans une conversation.
"""

from __future__ import annotations

import platform
import struct
import sys
from dataclasses import dataclass, field

from .data import init_mt5, terminaux_installes

# Noms sous lesquels les courtiers commercialisent l'or.
MOTIFS_OR = ("XAU", "GOLD", "OR.")


@dataclass
class Check:
    """Résultat d'une vérification."""

    name: str
    ok: bool | None  # None = information, ni succès ni échec
    detail: str
    hint: str = ""

    def to_text(self) -> str:
        marque = {True: "OK  ", False: "ECHEC", None: "info"}[self.ok]
        ligne = f"[{marque:<5}] {self.name:<28} {self.detail}"
        if self.hint:
            ligne += f"\n{'':>10}→ {self.hint}"
        return ligne


@dataclass
class Diagnostic:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok is not False for c in self.checks)

    def to_text(self) -> str:
        lignes = ["── Diagnostic smcbot ──────────────────────────────"]
        lignes += [c.to_text() for c in self.checks]
        lignes.append("───────────────────────────────────────────────────")
        lignes.append(
            "Tout est vert : lance `download`."
            if self.ok
            else "Corrige la première ligne ECHEC, puis relance `doctor`."
        )
        return "\n".join(lignes)


def run_diagnostics(
    symbol: str = "XAUUSD", timeframe: str = "M1", path: str | None = None
) -> Diagnostic:
    """Déroule les vérifications, en s'arrêtant net à la première bloquante."""
    diag = Diagnostic()

    bits = struct.calcsize("P") * 8
    diag.checks.append(
        Check(
            "Python",
            sys.version_info >= (3, 10) and bits == 64,
            f"{platform.python_version()} {bits} bits",
            ""
            if sys.version_info >= (3, 10) and bits == 64
            else "smcbot demande Python 3.10+ en 64 bits (MT5 ne fournit pas de "
            "version 32 bits).",
        )
    )

    windows = platform.system() == "Windows"
    diag.checks.append(
        Check(
            "Système",
            None,
            platform.system(),
            ""
            if windows
            else "Le module MetaTrader5 n'existe que sous Windows. Backtest et "
            "paper trading fonctionnent quand même à partir d'un CSV.",
        )
    )

    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:
        diag.checks.append(
            Check(
                "Paquet MetaTrader5",
                False,
                f"absent ({exc})",
                "Installe-le : pip install MetaTrader5"
                if windows
                else "Indisponible hors Windows : exporte un CSV depuis MT5 "
                "(voir bot/mt5/ExportBars.mq5).",
            )
        )
        return diag

    diag.checks.append(
        Check(
            "Paquet MetaTrader5",
            True,
            f"version {getattr(mt5, '__version__', 'inconnue')}",
        )
    )
    _check_terminal(mt5, diag, symbol, timeframe, path)
    return diag


def _check_terminal(
    mt5, diag: Diagnostic, symbol: str, timeframe: str, path: str | None = None
) -> None:
    """Vérifications qui exigent un terminal MT5 en fonctionnement."""
    try:
        init_mt5(mt5, path)
    except RuntimeError as exc:  # pragma: no cover - dépend de la plateforme
        premiere, _, reste = str(exc).partition("\n")
        diag.checks.append(
            Check("Connexion au terminal", False, premiere, reste)
        )
        _lister_terminaux(diag, path)
        return

    try:  # pragma: no cover - dépend de la plateforme
        info = mt5.terminal_info()
        if info is not None:
            diag.checks.append(
                Check(
                    "Terminal",
                    bool(info.connected),
                    f"{info.name} — {'connecté' if info.connected else 'hors ligne'}",
                    ""
                    if info.connected
                    else "Le terminal est ouvert mais sans connexion au serveur.",
                )
            )
            diag.checks.append(
                Check("Trading algorithmique", None,
                      "autorisé" if info.trade_allowed else "désactivé (sans effet "
                      "ici : smcbot n'envoie aucun ordre)")
            )

        compte = mt5.account_info()
        if compte is not None:
            reel = compte.trade_mode == getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2)
            diag.checks.append(
                Check(
                    "Compte",
                    None,
                    f"{'RÉEL' if reel else 'démo'}, devise {compte.currency}",
                    "La devise du compte détermine value_per_point_per_lot : "
                    "hors USD, vérifie cette valeur."
                    if compte.currency != "USD"
                    else "",
                )
            )

        _check_symbol(mt5, diag, symbol, timeframe)
    finally:  # pragma: no cover - dépend de la plateforme
        mt5.shutdown()


def _lister_terminaux(diag: Diagnostic, path: str | None) -> None:
    """Énumère les terminaux installés : en avoir plusieurs explique bien des échecs."""
    if path:
        diag.checks.append(
            Check("Terminal désigné", None, path,
                  "Vérifie que ce chemin est bien celui du MT5 où tu es connecté.")
        )
        return

    installes = terminaux_installes()
    if not installes:
        return
    if len(installes) == 1:
        diag.checks.append(Check("Terminal installé", None, str(installes[0])))
        return

    diag.checks.append(
        Check(
            "Terminaux installés",
            None,
            f"{len(installes)} trouvés",
            "Plusieurs terminaux : `initialize()` peut ouvrir le mauvais. "
            "Désigne le tien avec --mt5-path \"" + str(installes[0]) + "\" "
            "(les autres : " + ", ".join(str(p) for p in installes[1:4]) + ")",
        )
    )


def _check_symbol(mt5, diag: Diagnostic, symbol: str, timeframe: str) -> None:
    """Le symbole demandé existe-t-il, et son historique est-il disponible ?"""
    trouve = mt5.symbol_select(symbol, True)
    if not trouve:  # pragma: no cover - dépend de la plateforme
        candidats: list[str] = []
        for motif in MOTIFS_OR:
            for s in mt5.symbols_get(f"*{motif}*") or []:
                if s.name not in candidats:
                    candidats.append(s.name)
        diag.checks.append(
            Check(
                f"Symbole {symbol}",
                False,
                "introuvable chez ce courtier",
                f"Essaie l'un de ceux-ci : {', '.join(candidats[:10])}"
                if candidats
                else "Aucun symbole ressemblant à de l'or. Vérifie le nom dans "
                "la fenêtre Observation du marché.",
            )
        )
        return

    spec = mt5.symbol_info(symbol)  # pragma: no cover - dépend de la plateforme
    if spec is not None:
        valeur = spec.trade_tick_value
        if spec.trade_tick_size > 0:
            valeur = spec.trade_tick_value * (spec.point / spec.trade_tick_size)
        diag.checks.append(
            Check(
                f"Symbole {symbol}",
                True,
                f"{spec.digits} décimales, point {spec.point}, "
                f"{valeur:.4f} par point et par lot, spread {spec.spread} pts",
            )
        )

    tf = getattr(mt5, f"TIMEFRAME_{timeframe}", None)
    if tf is None:
        diag.checks.append(
            Check(f"Historique {timeframe}", False, "unité de temps inconnue")
        )
        return

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 5000)
    nombre = 0 if rates is None else len(rates)
    diag.checks.append(
        Check(
            f"Historique {timeframe}",
            nombre > 0,
            f"{nombre} bougies lues" if nombre else f"aucune ({mt5.last_error()})",
            ""
            if nombre
            else f"Ouvre un graphique {symbol} en {timeframe} et fais défiler "
            f"vers la gauche pour charger l'historique.",
        )
    )
