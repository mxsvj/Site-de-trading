"""Enregistre le carnet d'ordres de l'or, seul champ d'information non exploré.

Tout ce que le projet a mesuré vient du prix seul, et le prix ne porte rien
d'exploitable à aucune échelle : 30 s, 60 s, M1, M5. Le carnet est une variable
d'une autre nature — combien de volume attend de chaque côté, et à quelle
distance.

**MT5 ne donne aucun historique de carnet.** Contrairement aux ticks, qui
remontent à 30 mois, le carnet n'existe qu'à l'instant présent. Il faut donc
l'accumuler soi-même, et la mesure ne pourra pas être faite avant plusieurs
jours de collecte. C'est le prix d'entrée de cette piste.

Vérifié le 2026-08-12 : Pepperstone diffuse bien 10 niveaux sur XAUUSD (5 achat,
5 vente). Deux sondes antérieures avaient conclu le contraire — l'une mesurait
un marché en coupure quotidienne, l'autre détectait mal la réouverture. Ne pas
conclure à l'absence de carnet sans avoir vérifié qu'un tick vient de bouger.

On enregistre les niveaux bruts plutôt qu'un déséquilibre déjà calculé : la
bonne définition du déséquilibre n'est pas connue d'avance, et un fichier de
niveaux permet de les essayer toutes après coup, sans recollecter.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import time
from pathlib import Path

NIVEAUX = 5


def entetes() -> list[str]:
    cols = ["time", "bid", "ask"]
    for cote in ("ask", "bid"):
        for i in range(1, NIVEAUX + 1):
            cols += [f"{cote}_p{i}", f"{cote}_v{i}"]
    return cols


def ligne(book, tick) -> list | None:
    """Aplatit un carnet en une ligne, du meilleur prix au plus éloigné."""
    ventes = sorted(
        (e for e in book if e.type == 1), key=lambda e: e.price
    )[:NIVEAUX]
    achats = sorted(
        (e for e in book if e.type == 2), key=lambda e: -e.price
    )[:NIVEAUX]
    if len(ventes) < NIVEAUX or len(achats) < NIVEAUX:
        return None
    valeurs = [
        dt.datetime.fromtimestamp(tick.time, tz=dt.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        tick.bid,
        tick.ask,
    ]
    for cote in (ventes, achats):
        for e in cote:
            valeurs += [e.price, e.volume]
    return valeurs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--out", required=True)
    ap.add_argument("--heures", type=float, default=8.0)
    ap.add_argument(
        "--periode", type=float, default=0.25, help="secondes entre deux relevés"
    )
    args = ap.parse_args()

    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 init KO : {mt5.last_error()}")

    chemin = Path(args.out)
    neuf = not chemin.exists() or chemin.stat().st_size == 0
    fh = chemin.open("a", newline="", encoding="utf-8")
    writer = csv.writer(fh)
    if neuf:
        writer.writerow(entetes())

    try:
        if not mt5.symbol_select(args.symbol, True):
            raise SystemExit(f"Symbole {args.symbol} introuvable.")
        if not mt5.market_book_add(args.symbol):
            raise SystemExit(f"Souscription refusée : {mt5.last_error()}")

        fin = time.time() + args.heures * 3600
        ecrites = vides = 0
        dernier_rapport = time.time()
        # Un carnet identique au précédent n'apporte rien : on ne garde que les
        # changements, ce qui divise la taille du fichier sans perdre
        # d'information — l'horodatage suffit à reconstruire la durée d'un état.
        precedent = None

        while time.time() < fin:
            book = mt5.market_book_get(args.symbol)
            tick = mt5.symbol_info_tick(args.symbol)
            if book and tick:
                l = ligne(book, tick)
                if l is not None and l[1:] != precedent:
                    writer.writerow(l)
                    precedent = l[1:]
                    ecrites += 1
            else:
                vides += 1

            if time.time() - dernier_rapport >= 300:
                fh.flush()
                print(
                    f"  {dt.datetime.now(dt.timezone.utc):%H:%M} — "
                    f"{ecrites:,} états enregistrés, {vides:,} relevés vides",
                    flush=True,
                )
                dernier_rapport = time.time()
            time.sleep(args.periode)

        mt5.market_book_release(args.symbol)
    finally:
        fh.close()
        mt5.shutdown()

    print(f"\n{ecrites:,} états de carnet écrits dans {chemin}")
    if ecrites == 0:
        print("Aucun état : vérifier que le marché cotait pendant la collecte.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
