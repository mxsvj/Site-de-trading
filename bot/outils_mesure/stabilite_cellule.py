"""Une cellule d'`edge-scan` tient-elle dans le temps, ou par bouffées ?

Une cellule peut franchir le seuil sur une période et rien sur une autre. Deux
lectures opposées : un effet réel qui apparaît avec un régime de marché, ou du
hasard qui a trouvé sa fenêtre. Les départager demande de regarder période par
période plutôt qu'en deux blocs.

Motivé par `momentum récent = Q1` sur le M1 : t = 0,61 en étude, t = 3,54 en
contrôle. L'inverse du motif habituel — normalement une cellule est forte en
étude et s'effondre ensuite. Ici il faut savoir si le contrôle est un régime ou
un accident.

Le balayage lui-même est réutilisé tel quel (`edge.balayer`) : réimplémenter les
définitions de momentum, d'ATR ou de quintile ferait diverger la mesure de ce
qu'elle prétend reproduire.

Les quintiles sont recalculés sur chaque sous-période, ce qui est le sens de la
question : « dans ce régime, le quintile bas fait-il mieux que la moyenne de ce
régime ? »
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smcbot.data import load_csv  # noqa: E402
from smcbot.edge import balayer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--critere", default="momentum récent")
    ap.add_argument("--valeur", default="Q1")
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--tranches", type=int, default=8)
    ap.add_argument(
        "--atr-points", type=float, default=128.0,
        help="ATR médian en points, pour convertir l'effet en points",
    )
    ap.add_argument(
        "--spread", type=float, default=18.0, help="frais, en points"
    )
    args = ap.parse_args()

    bougies = load_csv(args.csv)
    print(f"{len(bougies):,} bougies — {bougies[0].time} → {bougies[-1].time}")
    print(f"Cellule suivie : {args.critere} = {args.valeur}, horizon {args.horizon}\n")

    taille = len(bougies) // args.tranches
    seuil_frais = args.spread / args.atr_points

    print(
        f"{'période':<24}{'n':>8}{'excès (ATR)':>13}{'points':>9}{'t':>8}"
        f"{'seuil':>8}"
    )
    print("-" * 70)

    positifs = 0
    mesurees = 0
    for k in range(args.tranches):
        debut = k * taille
        fin = (k + 1) * taille if k < args.tranches - 1 else len(bougies)
        tranche = bougies[debut:fin]
        if len(tranche) < 5000:
            continue
        balayage = balayer(tranche, horizon=args.horizon)
        cible = next(
            (
                c
                for c in balayage.examinees
                if c.critere == args.critere and c.valeur == args.valeur
            ),
            None,
        )
        etiquette = (
            f"{tranche[0].time:%Y-%m-%d} → {tranche[-1].time:%m-%d}"
        )
        if cible is None:
            print(f"{etiquette:<24}{'cellule absente':>8}")
            continue
        mesurees += 1
        if cible.exces > 0:
            positifs += 1
        marque = "*" if abs(cible.t) > balayage.seuil else " "
        print(
            f"{etiquette:<24}{cible.n:>8,}{cible.exces:>+12.3f}A"
            f"{cible.exces * args.atr_points:>+9.1f}{cible.t:>8.2f}"
            f"{balayage.seuil:>8.2f}{marque}"
        )

    print(
        f"\n{positifs}/{mesurees} sous-périodes de signe positif.\n"
        f"Seuil de rentabilité : {seuil_frais:.3f} ATR "
        f"= {args.spread:.0f} points de frais."
    )
    print(
        "\nUn effet réel se voit sur la majorité des sous-périodes, même sans\n"
        "franchir le seuil sur chacune — la puissance y est plus faible.\n"
        "Un effet concentré sur une seule tranche est une bouffée, pas un\n"
        "régime."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
