"""Reconstruit des bougies M1 depuis les ticks, pour casser le plafond du courtier.

CLAUDE.md donne le M1 pour indécidable : le balayage `edge-scan` y détecte
0,167 ATR quand il en faudrait 0,126, donc il faudrait environ deux fois plus
d'observations — et « le courtier plafonnant à 100 000 bougies (≈ 101 jours en
M1), la seule voie est d'accumuler l'historique dans le temps ».

Ce plafond porte sur `copy_rates`, pas sur `copy_ticks_range`. Sondage du
2026-08-10 : `copy_rates` refuse au-delà de 100 000 bougies, mais les ticks
remontent à au moins 30 mois. L'historique exploitable est donc environ neuf
fois plus profond que ce que le projet utilisait, et il est disponible tout de
suite au lieu d'être attendu.

Les bougies sont construites sur le **bid**, comme celles de MetaTrader : le
spread ne doit pas entrer deux fois dans les calculs, une fois dans la bougie et
une fois dans les frais.

Le temps reste celui du serveur (UTC+3 chez Pepperstone), comme pour `download`,
pour que `--tz-shift -3` continue de s'appliquer de la même façon.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smcbot.data import agreger_ticks, debut_de_bougie as _minute  # noqa: E402


def _dernier_instant(chemin: Path) -> int | None:
    """Dernière bougie déjà écrite, pour reprendre sans tout refaire."""
    if not chemin.exists() or chemin.stat().st_size == 0:
        return None
    dernier = None
    with chemin.open("r", encoding="utf-8") as fh:
        for ligne in csv.reader(fh):
            if ligne and ligne[0] != "time":
                dernier = ligne[0]
    if dernier is None:
        return None
    moment = dt.datetime.strptime(dernier, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=dt.timezone.utc
    )
    return int(moment.timestamp())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--mois", type=int, default=12, help="profondeur en mois")
    ap.add_argument(
        "--secondes", type=int, default=60, help="durée d'une bougie (60 = M1)"
    )
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--tranche", type=int, default=3, help="jours téléchargés d'un coup"
    )
    args = ap.parse_args()

    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 init KO : {mt5.last_error()}")

    chemin = Path(args.out)
    reprise = _dernier_instant(chemin)

    # Les bornes de tranches doivent tomber sur des débuts de bougie. Sinon une
    # minute est coupée en deux, chaque moitié part dans une tranche différente,
    # et l'ouverture reconstruite est celle du milieu de la minute — erreur
    # mesurée jusqu'à 165 points avant correction. Le pas en jours est un
    # multiple entier de la durée d'une bougie, donc l'alignement se propage.
    maintenant = int(dt.datetime.now(dt.timezone.utc).timestamp())
    fin = dt.datetime.fromtimestamp(
        _minute(maintenant, args.secondes), tz=dt.timezone.utc
    )
    debut = fin - dt.timedelta(days=30 * args.mois)
    if reprise is not None:
        # On repart de la bougie suivante : la dernière écrite peut être
        # incomplète si la tranche s'est arrêtée en plein milieu.
        debut = dt.datetime.fromtimestamp(reprise, tz=dt.timezone.utc)
        print(f"Reprise à partir de {debut:%Y-%m-%d %H:%M}.")

    nouveau = reprise is None
    fh = chemin.open("w" if nouveau else "a", newline="", encoding="utf-8")
    writer = csv.writer(fh)
    if nouveau:
        writer.writerow(["time", "open", "high", "low", "close", "volume"])

    try:
        if not mt5.symbol_select(args.symbol, True):
            raise SystemExit(f"Symbole {args.symbol} introuvable.")

        total_bougies = 0
        total_ticks = 0
        curseur = debut
        pas = dt.timedelta(days=args.tranche)
        while curseur < fin:
            borne = min(curseur + pas, fin)
            ticks = mt5.copy_ticks_range(
                args.symbol, curseur, borne, mt5.COPY_TICKS_INFO
            )
            if ticks is not None and len(ticks):
                total_ticks += len(ticks)
                # Les bornes tombant sur des débuts de bougie, aucune minute
                # n'est coupée : toutes les barres de la tranche sont complètes.
                # Seule la toute dernière, celle de la minute en cours, ne l'est
                # pas — on l'écarte.
                # Les bornes tombant sur des débuts de bougie, aucune bougie
                # n'est coupée : toutes celles de la tranche sont complètes.
                # Seule la dernière de la toute dernière tranche est encore en
                # formation.
                barres = agreger_ticks(
                    ticks, args.secondes, complete_seulement=(borne >= fin)
                )
                for b in barres:
                    writer.writerow(
                        [
                            b.time.strftime("%Y-%m-%d %H:%M:%S"),
                            b.open, b.high, b.low, b.close, b.volume,
                        ]
                    )
                    total_bougies += 1
                fh.flush()
            print(
                f"  {curseur:%Y-%m-%d} → {borne:%Y-%m-%d} : "
                f"{total_bougies:,} bougies, {total_ticks:,} ticks",
                flush=True,
            )
            curseur = borne
    finally:
        fh.close()
        mt5.shutdown()

    print(
        f"\n{total_bougies:,} bougies de {args.secondes} s écrites dans "
        f"{chemin}\ndepuis {total_ticks:,} ticks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
