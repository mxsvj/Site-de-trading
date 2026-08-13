"""Le seuil choisi sans regarder le contrôle tient-il sur le contrôle ?

`selectivite.py` a trouvé un rapport avantage/coût supérieur à 1 en resserrant
la sélection — mais le seuil a été choisi **en regardant les résultats du
contrôle**. C'est de la sélection déguisée en validation : avec huit seuils
essayés, l'un d'eux ressort forcément.

Le test honnête fixe tout sur l'étude — le score, sa normalisation, et la valeur
de coupure — puis l'applique au contrôle sans plus rien ajuster. On ne compare
plus des quantiles, qui se recalculent sur chaque période, mais **une valeur de
score absolue**, décidée avant d'avoir vu la suite.

Second examen indispensable : le régime de spread. À 1 % de sélectivité,
l'avantage n'existait que dans les moments chers — +2,2 points pour 11 de spread
dans le régime bon marché, contre +16,6 pour 31 dans le régime cher. Un avantage
qui ne vit que là où l'exécution coûte 31 points ne se trade pas, d'autant que
le spread affiché sous-estime le glissement réel dans ces instants-là.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path


def stats(echantillon, globale):
    r = [x[1] - globale for x in echantillon]
    if len(r) < 2:
        return None
    moyenne = statistics.fmean(r)
    sp = statistics.fmean(x[2] for x in echantillon)
    return {
        "n": len(r),
        "exces": moyenne,
        "t": moyenne / (statistics.stdev(r) / len(r) ** 0.5),
        "spread": sp,
        "rapport": moyenne / sp if sp > 0 else 0.0,
        "gagnants": sum(1 for x in r if x > 0) / len(r),
    }


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
    etude, controle = lignes[:coupe], lignes[coupe:]
    g_e = statistics.fmean(x[1] for x in etude)
    g_c = statistics.fmean(x[1] for x in controle)

    print(f"Étude {len(etude):,}, contrôle {len(controle):,}.\n")
    print("── Seuil fixé sur l'étude, appliqué tel quel au contrôle ──")
    print(
        f"{'seuil (score)':<15}{'n étude':>9}{'rapport':>9}  │"
        f"{'n contrôle':>11}{'excès':>9}{'t':>7}{'spread':>8}{'rapport':>9}"
    )
    print("-" * 82)

    tri_e = sorted(etude, key=lambda x: x[3])
    for part in (0.01, 0.005, 0.002, 0.001, 0.0005):
        k = int(len(tri_e) * part)
        if k < 30:
            continue
        # La coupure est une valeur de score, décidée sur l'étude seule.
        seuil = tri_e[-k][3]
        s_e = stats(tri_e[-k:], g_e)
        retenus = [x for x in controle if x[3] >= seuil]
        s_c = stats(retenus, g_c)
        if s_c is None:
            print(f"{seuil:<15.3f}{s_e['n']:>9,}{s_e['rapport']:>9.2f}  │"
                  f"{len(retenus):>11,}   trop peu")
            continue
        marque = "  ← couvre" if s_c["rapport"] > 1 else ""
        print(
            f"{seuil:<15.3f}{s_e['n']:>9,}{s_e['rapport']:>9.2f}  │"
            f"{s_c['n']:>11,}{s_c['exces']:>+8.1f}p{s_c['t']:>7.2f}"
            f"{s_c['spread']:>7.0f}p{s_c['rapport']:>9.2f}{marque}"
        )

    # ── L'avantage survit-il dans les moments réellement traitables ? ────
    print("\n── Contrôle, seuil de l'étude à 0,1 %, découpé par spread ──")
    k = max(30, int(len(tri_e) * 0.001))
    seuil = tri_e[-k][3]
    retenus = [x for x in controle if x[3] >= seuil]
    print(f"{'régime':<16}{'n':>7}{'excès':>9}{'spread':>8}{'rapport':>9}")
    print("-" * 50)
    for nom, bas, haut in (
        ("≤ 15 pts", 0, 15), ("16 à 25 pts", 15, 25),
        ("26 à 40 pts", 25, 40), ("> 40 pts", 40, 10_000),
    ):
        seg = [x for x in retenus if bas < x[2] <= haut]
        s = stats(seg, g_c)
        if s is None or s["n"] < 30:
            print(f"{nom:<16}{len(seg):>7,}   trop peu d'observations")
            continue
        print(
            f"{nom:<16}{s['n']:>7,}{s['exces']:>+8.1f}p{s['spread']:>7.0f}p"
            f"{s['rapport']:>9.2f}"
        )

    print(
        "\nUn avantage qui n'existe qu'au-delà de 25 points de spread ne se\n"
        "trade pas : ces instants sont ceux des annonces, où le spread affiché\n"
        "sous-estime le glissement, où les ordres passent mal, et où rien ne\n"
        "garantit d'être servi au prix mesuré."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
