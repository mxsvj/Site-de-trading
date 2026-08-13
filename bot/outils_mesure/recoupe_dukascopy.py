"""Nos mesures décrivent-elles le marché, ou seulement Pepperstone ?

Toutes les conclusions du projet reposent sur un seul flux. Deux d'entre elles
seraient fragiles si ce flux était particulier :

- le **profil de spread** (11 points en séance, 16 à 17 hors séance, 12 à 24
  selon l'époque), qui fixe le seuil de rentabilité de toutes les mesures ;
- le constat que **l'avantage ne vit que dans les moments à spread élevé**, qui
  pourrait n'être qu'un artefact d'élargissement propre à un courtier.

Dukascopy fournit bid **et** ask sur une vingtaine d'années, indépendamment.
Vérifié le 2026-08-12 : couverture M1 pleine depuis ~2005, côté ask disponible.

Deux recoupements ici :
1. **les prix** — nos rendements M1 et les leurs décrivent-ils le même marché ?
2. **le spread** — leur profil horaire ressemble-t-il au nôtre ?

Attention à l'usage : Dukascopy est un **autre courtier**, avec son propre
modèle d'exécution. Ses spreads ne chiffrent pas nos coûts et ne doivent jamais
remplacer les nôtres dans un seuil de rentabilité. Ils servent à savoir si la
*forme* de ce qu'on observe est une propriété du marché ou une particularité
de Pepperstone.

Horaires : Dukascopy publie en UTC, notre serveur est en UTC+3. Les comparer
sans décaler ferait glisser tout le profil horaire de trois heures.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import statistics
from collections import defaultdict
from pathlib import Path


def charger_pepperstone(chemin: str, decalage_h: float):
    """Bougies M1 du projet, ramenées en UTC."""
    par_minute = {}
    with Path(chemin).open(encoding="utf-8-sig") as fh:
        for l in csv.DictReader(fh):
            t = dt.datetime.strptime(l["time"], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=dt.timezone.utc
            ) + dt.timedelta(hours=decalage_h)
            par_minute[int(t.timestamp())] = float(l["close"])
    return par_minute


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True, help="M1 Pepperstone du projet")
    ap.add_argument("--tz-shift", type=float, default=-3.0)
    ap.add_argument("--jours", type=int, default=45)
    args = ap.parse_args()

    import truststore

    truststore.inject_into_ssl()  # cette machine intercepte le TLS
    import dukascopy_python as dk
    from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD as XAU

    fin = dt.datetime.now() - dt.timedelta(days=2)
    debut = fin - dt.timedelta(days=args.jours)
    print(f"Dukascopy XAUUSD M1, {debut:%Y-%m-%d} → {fin:%Y-%m-%d}")

    bid = dk.fetch(XAU, dk.INTERVAL_MIN_1, dk.OFFER_SIDE_BID, debut, fin)
    ask = dk.fetch(XAU, dk.INTERVAL_MIN_1, dk.OFFER_SIDE_ASK, debut, fin)
    if bid is None or ask is None or len(bid) == 0:
        raise SystemExit("Dukascopy n'a rien renvoyé.")
    print(f"  {len(bid):,} bougies bid, {len(ask):,} bougies ask\n")

    duka_bid, duka_spread = {}, {}
    for horodatage, ligne in bid.iterrows():
        duka_bid[int(horodatage.timestamp())] = float(ligne["close"])
    for horodatage, ligne in ask.iterrows():
        s = int(horodatage.timestamp())
        if s in duka_bid:
            duka_spread[s] = (float(ligne["close"]) - duka_bid[s]) / 0.01

    # ── 1. Les deux flux décrivent-ils le même marché ? ──────────────────
    pepper = charger_pepperstone(args.csv, args.tz_shift)
    communs = sorted(set(duka_bid) & set(pepper))
    print(f"── Recoupement des prix — {len(communs):,} minutes communes ──")
    if len(communs) < 500:
        print("  recouvrement insuffisant pour conclure")
    else:
        ecarts = [abs(duka_bid[s] - pepper[s]) / 0.01 for s in communs]
        r_d, r_p = [], []
        for a, b in zip(communs, communs[1:]):
            if b - a == 60:
                r_d.append((duka_bid[b] - duka_bid[a]) / 0.01)
                r_p.append((pepper[b] - pepper[a]) / 0.01)
        if len(r_d) > 100:
            md, mp = statistics.fmean(r_d), statistics.fmean(r_p)
            num = sum((x - md) * (y - mp) for x, y in zip(r_d, r_p))
            dd = sum((x - md) ** 2 for x in r_d) ** 0.5
            dp = sum((y - mp) ** 2 for y in r_p) ** 0.5
            correl = num / (dd * dp) if dd and dp else 0.0
            print(f"  écart de niveau médian   : {statistics.median(ecarts):.0f} points")
            print(f"  corrélation des rendements M1 : {correl:.4f}")
            print(
                "  → proche de 1 : nos mesures portent sur le marché,\n"
                "    pas sur une particularité de cotation."
                if correl > 0.95
                else "  → corrélation faible : les deux flux divergent, à creuser."
            )

    # ── 2. Le profil de spread a-t-il la même forme ? ────────────────────
    print("\n── Profil de spread Dukascopy, heure par heure (UTC) ──")
    par_heure = defaultdict(list)
    for s, sp in duka_spread.items():
        if 0 < sp < 500:
            par_heure[dt.datetime.fromtimestamp(s, tz=dt.timezone.utc).hour].append(sp)

    valides = {h: v for h, v in par_heure.items() if len(v) >= 100}
    if not valides:
        print("  pas assez de données")
        return 1
    medians = {h: statistics.median(v) for h, v in valides.items()}
    creux = min(medians, key=medians.get)
    plein = max(medians, key=medians.get)
    print(f"{'heure':<8}{'n':>8}{'médian':>9}")
    print("-" * 27)
    for h in sorted(medians):
        print(f"{h:>3} h  {len(valides[h]):>9,}{medians[h]:>9.1f}")
    print(
        f"\n  moins cher : {creux:02d} h à {medians[creux]:.1f} points\n"
        f"  plus cher  : {plein:02d} h à {medians[plein]:.1f} points, soit "
        f"{medians[plein] / medians[creux]:.1f} fois plus"
    )
    print(
        "\n  Chez Pepperstone : 11 points en séance, 16 à 17 hors séance,\n"
        "  rapport 1,5. Un rapport voisin signifie que la respiration du\n"
        "  spread est une propriété du marché de l'or, pas de notre courtier.\n"
        "  Le **niveau**, lui, reste propre à chaque courtier : ne jamais\n"
        "  substituer ces chiffres aux nôtres dans un seuil de rentabilité."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
