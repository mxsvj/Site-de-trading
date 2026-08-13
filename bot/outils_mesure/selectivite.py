"""Le rapport avantage/coût s'améliore-t-il assez avec la sélectivité ?

Constat récurrent du projet : l'avantage et le coût varient ensemble. Aux
quintiles, le rapport vaut 0,08 ; dans la queue à 1 % du régime cher, il monte à
0,54. S'il continuait de croître, un seuil encore plus serré finirait par
dépasser 1 et une stratégie deviendrait possible.

Ce script pousse la sélectivité jusqu'à 0,02 % pour voir si le rapport franchit
1 ou s'il sature en dessous. Il lit le cache produit par `combinaison_signaux`,
donc il ne recollecte rien.

Le rapport est calculé sur le **spread réellement observé dans la queue**, pas
sur la moyenne de la période : les observations extrêmes se concentrent dans les
moments chers, et prendre la moyenne flatterait le résultat d'un facteur 1,5.

Lu sur la période de contrôle uniquement. Resserrer un seuil sur l'étude
fabrique des chiffres qui ne survivent pas ailleurs — la queue à 1 % de la
mesure argent/or donnait +15,3 points en étude et +5,8 hors échantillon.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path

SEUILS = (0.20, 0.05, 0.01, 0.005, 0.002, 0.001, 0.0005, 0.0002)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--split", type=float, default=0.6)
    args = ap.parse_args()

    lignes = []
    with Path(args.cache).open(encoding="utf-8") as fh:
        for l in csv.DictReader(fh):
            lignes.append(
                (int(l["t"]), float(l["r"]), float(l["spread"]), float(l["score"]))
            )
    lignes.sort(key=lambda x: x[0])
    coupe = int(len(lignes) * args.split)
    controle = lignes[coupe:]
    globale = statistics.fmean(x[1] for x in controle)
    print(
        f"{len(lignes):,} observations, contrôle {len(controle):,}.\n"
        f"Rendement inconditionnel du contrôle : {globale:+.2f} points.\n"
    )

    tri = sorted(controle, key=lambda x: x[3])
    print(
        f"{'sélectivité':<14}{'n':>8}{'excès':>9}{'t':>7}"
        f"{'spread réel':>13}{'avantage/coût':>15}"
    )
    print("-" * 68)
    for part in SEUILS:
        k = int(len(tri) * part)
        if k < 30:
            print(f"{part:<14.4%}{k:>8,}   moins de 30 observations, non jugé")
            continue
        queue = tri[-k:]
        r = [x[1] for x in queue]
        exces = statistics.fmean(r) - globale
        t = exces / (statistics.stdev(r) / k**0.5)
        sp = statistics.fmean(x[2] for x in queue)
        rapport = exces / sp if sp > 0 else 0.0
        marque = "  ← couvre" if rapport > 1 else ""
        print(
            f"{part:<14.4%}{k:>8,}{exces:>+8.1f}p{t:>7.2f}{sp:>12.0f}p"
            f"{rapport:>15.2f}{marque}"
        )

    print(
        "\nLe rapport doit dépasser 1 pour qu'une position couvre seulement son\n"
        "spread, avant le moindre gain — et il faudrait aller bien au-delà pour\n"
        "absorber le glissement et l'arrondi du lot. S'il sature sous 1 en\n"
        "perdant sa significativité, resserrer davantage ne mène nulle part."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
