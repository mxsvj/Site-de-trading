"""Une moyenne de queue tient-elle à quelques observations ?

Poussée à 0,1 % de sélectivité, la queue du score combiné affiche +61,4 points
contre 35 de spread — le seul rapport supérieur à 1 du projet. Avant d'y croire,
il faut savoir **de quoi cette moyenne est faite**.

Sur des rendements à 30 secondes, une poignée de pics d'annonce peut porter
toute la moyenne. Trois symptômes le trahissent :

- la **médiane** loin sous la moyenne : quelques valeurs énormes tirent tout ;
- un **taux de gagnants proche de 50 %** : aucune régularité, juste de la
  variance ;
- une **concentration dans le temps** : quelques journées portent le total, donc
  l'avantage n'est pas reproductible, il a été observé une fois.

Un stop tronquerait d'ailleurs ces pics, alors qu'il ne tronquerait pas les
pertes symétriques — une moyenne portée par des extrêmes ne survit pas à une
exécution réelle.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import statistics
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--split", type=float, default=0.6)
    ap.add_argument(
        "--seuils", default="0.01,0.005,0.001,0.0005",
        help="sélectivités à examiner",
    )
    args = ap.parse_args()

    lignes = []
    with Path(args.cache).open(encoding="utf-8") as fh:
        for l in csv.DictReader(fh):
            lignes.append(
                (int(l["t"]), float(l["r"]), float(l["spread"]), float(l["score"]))
            )
    lignes.sort(key=lambda x: x[0])
    controle = lignes[int(len(lignes) * args.split):]
    globale = statistics.fmean(x[1] for x in controle)
    tri = sorted(controle, key=lambda x: x[3])

    for part in [float(s) for s in args.seuils.split(",")]:
        k = int(len(tri) * part)
        if k < 30:
            continue
        queue = tri[-k:]
        r = [x[1] - globale for x in queue]
        jours = Counter(
            dt.datetime.fromtimestamp(x[0], tz=dt.timezone.utc).strftime("%Y-%m-%d")
            for x in queue
        )
        gagnants = sum(1 for x in r if x > 0) / len(r)
        # Que reste-t-il si l'on retire les cinq plus gros mouvements ? S'ils
        # portent l'essentiel, la moyenne ne décrit aucune régularité.
        sans_extremes = sorted(r)[:-5] if len(r) > 10 else r

        print(f"\n── sélectivité {part:.2%} — {k:,} observations ──")
        print(f"  moyenne                 : {statistics.fmean(r):+8.1f} p")
        print(f"  médiane                 : {statistics.median(r):+8.1f} p")
        print(f"  gagnants                : {gagnants:8.1%}")
        print(
            f"  moyenne sans les 5 plus : {statistics.fmean(sans_extremes):+8.1f} p"
        )
        print(f"  spread moyen            : "
              f"{statistics.fmean(x[2] for x in queue):8.0f} p")
        print(f"  journées distinctes     : {len(jours):8,}")
        top = jours.most_common(3)
        part_top = sum(n for _, n in top) / k
        print(
            f"  3 journées les + fournies : "
            f"{', '.join(f'{j} ({n})' for j, n in top)}  → {part_top:.0%} du total"
        )

    print(
        "\nLecture : si la médiane est proche de zéro, si le taux de gagnants\n"
        "avoisine 50 %, ou si retirer cinq mouvements efface la moyenne, alors\n"
        "il n'y a pas d'avantage — il y a eu quelques grands mouvements."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
