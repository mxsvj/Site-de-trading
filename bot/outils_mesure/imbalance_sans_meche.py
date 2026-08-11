"""Une bougie sans mèche attire-t-elle vraiment le prix à combler son bord ?

Hypothèse reprise d'un projet antérieur (`bot-scalping-gold/no_wick.py`), qui la
testait sur M15 — hors de nos contraintes. Portée ici en M1.

Prémisse : une bougie qui ferme sans mèche d'un côté laisse une « imbalance »
que le prix reviendrait combler, c'est-à-dire retoucher le bord sans mèche.
Pour une bougie verte sans mèche basse, ce bord est son ouverture, qui vaut
aussi son plus bas.

**Le test original manque son témoin, et c'est ce qui le rend ininterprétable.**
Il mesure « le prix revient sur le bord dans X % des cas » sans jamais mesurer à
quelle fréquence il revient sur l'ouverture d'une bougie *quelconque*. Or le
prix revient constamment sur ses pas : un taux de 80 % peut très bien être
inférieur au hasard. On compare donc chaque mesure à un témoin apparié —
mêmes bougies, même cible (l'ouverture), sans la condition « sans mèche ».

Les hypothèses défavorables du projet sont conservées :
- entrée à la **clôture** de la bougie de signal, en payant le spread ;
- les extrêmes de la bougie d'entrée appartiennent au passé, on ne les applique
  pas au trade ;
- en cas d'ambiguïté sur une bougie, le **stop l'emporte** sur l'objectif.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smcbot.data import load_csv  # noqa: E402

FENETRES = [3, 6, 20, 50]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--point", type=float, default=0.01)
    ap.add_argument(
        "--tolerance", type=float, default=5.0,
        help="mèche tolérée pour dire « sans mèche », en points",
    )
    ap.add_argument("--spread", type=float, default=12.0)
    ap.add_argument(
        "--min-distance", type=float, default=0.0,
        help="corps minimal, en points. Un corps de 20 points est injouable : "
        "le spread en mange 60 %%. Le filtre s'applique aussi au témoin, "
        "sinon on comparerait deux populations différentes.",
    )
    args = ap.parse_args()

    bougies = load_csv(args.csv)
    n = len(bougies)
    tol = args.tolerance * args.point
    o = [b.open for b in bougies]
    h = [b.high for b in bougies]
    bas = [b.low for b in bougies]
    c = [b.close for b in bougies]

    print(f"{n:,} bougies — {bougies[0].time} → {bougies[-1].time}")
    print(f"Tolérance de mèche : {args.tolerance:g} points\n")

    # ── Détection ────────────────────────────────────────────────────────
    # +1 : verte sans mèche basse, imbalance sous le prix, on vendrait vers
    # l'ouverture. −1 : rouge sans mèche haute, miroir.
    mini = args.min_distance * args.point
    signaux = []
    for i in range(n):
        if abs(c[i] - o[i]) < mini:
            continue
        if c[i] > o[i] and (o[i] - bas[i]) <= tol:
            signaux.append((i, 1))
        elif c[i] < o[i] and (h[i] - o[i]) <= tol:
            signaux.append((i, -1))
    if mini > 0:
        print(f"Corps minimal exigé : {args.min_distance:g} points")

    print(
        f"Bougies sans mèche : {len(signaux):,} "
        f"({100 * len(signaux) / n:.1f} % des bougies)\n"
    )
    if len(signaux) < 100:
        raise SystemExit("Trop peu de signaux.")

    # ── (1) Taux de remplissage, contre témoin apparié ──────────────────
    # Témoin : toutes les bougies, même cible (l'ouverture). Sans lui, un taux
    # élevé ne prouve rien — le prix revient sans cesse sur ses pas.
    print("── Le prix revient-il sur l'ouverture ? ──")
    print(f"{'fenêtre':>9}{'sans mèche':>13}{'témoin':>10}{'écart':>9}")
    print("-" * 42)
    for W in FENETRES:
        atteint = sum(
            1
            for (i, _) in signaux
            if any(
                bas[j] <= o[i] <= h[j] for j in range(i + 1, min(i + 1 + W, n))
            )
        )
        # Témoin sur un sous-échantillon régulier, pour rester rapide et non
        # biaisé : une bougie sur 20, quelle que soit sa forme.
        # Le témoin subit le même filtre de taille : comparer des bougies de
        # tout corps à des signaux à gros corps opposerait deux populations.
        temoins = [
            i
            for i in range(0, n - W - 1, 20)
            if abs(c[i] - o[i]) >= mini
        ]
        atteint_t = sum(
            1
            for i in temoins
            if any(
                bas[j] <= o[i] <= h[j] for j in range(i + 1, min(i + 1 + W, n))
            )
        )
        taux = atteint / len(signaux)
        taux_t = atteint_t / len(temoins) if temoins else 0.0
        print(
            f"{W:>8}b{taux:>12.1%}{taux_t:>10.1%}{taux - taux_t:>+9.1%}"
        )

    # ── (2) Le trade de comblement ───────────────────────────────────────
    # Entrée à la clôture, objectif le bord, stop symétrique (RR 1:1).
    print("\n── Trade de comblement, RR 1:1, entrée au marché ──")
    print(f"{'spread':>8}{'trades':>9}{'réussite':>11}{'espérance':>12}")
    print("-" * 40)
    for spread in (0.0, args.spread, 18.0):
        cout = spread * args.point
        resultats = []
        for (i, d) in signaux:
            bord = o[i]
            distance = abs(c[i] - bord)
            if distance <= 0:
                continue
            if d == 1:  # imbalance dessous : on vend vers le bord
                tp, sl = bord, c[i] + distance
            else:
                tp, sl = bord, c[i] - distance
            issue = None
            # La bougie d'entrée est close : ses extrêmes sont antérieurs à
            # l'entrée, on démarre à la suivante.
            for j in range(i + 1, n):
                if d == 1:
                    if h[j] >= sl:      # stop d'abord : hypothèse défavorable
                        issue = -distance
                        break
                    if bas[j] <= tp:
                        issue = distance
                        break
                else:
                    if bas[j] <= sl:
                        issue = -distance
                        break
                    if h[j] >= tp:
                        issue = distance
                        break
            if issue is None:
                continue
            resultats.append((issue - cout) / distance)
        if not resultats:
            print(f"{spread:>7.0f} aucun trade")
            continue
        reussite = sum(1 for x in resultats if x > 0) / len(resultats)
        esperance = statistics.fmean(resultats)
        marque = "  ←" if esperance > 0 else ""
        print(
            f"{spread:>7.0f}{len(resultats):>9,}{reussite:>10.1%}"
            f"{esperance:>+11.3f}R{marque}"
        )

    print(
        "\nLecture : le taux de remplissage ne vaut que comparé à son témoin.\n"
        "Un écart nul signifie que « sans mèche » n'apporte aucune information,\n"
        "quelle que soit la valeur absolue du taux."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
