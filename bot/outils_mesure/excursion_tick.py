"""Combien l'or bouge-t-il en quelques secondes, et à quel prix ?

Question ouverte par la mesure du spread du 2026-08-10. À 12 points, un stop de
40 points ne coûte plus 60 % du risque mais 30 % : les stops serrés redeviennent
finançables. Mais sur M1 l'amplitude médiane d'une bougie est de 177 points — un
stop de 40 points y est du bruit pur. Si les stops serrés ont un sens, c'est donc
sous la minute, et personne ne l'a mesuré ici.

Ce script ne teste aucune stratégie et ne consomme aucune hypothèse : il mesure
de combien le prix se déplace en h secondes, pour h de 1 à 120. On en déduit,
pour chaque horizon, le stop qu'il faudrait poser et la part du risque que le
spread mangerait.

Aucune direction n'est supposée : c'est |Δprix|, pas un rendement signé.
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
from collections import defaultdict

HORIZONS_S = [1, 2, 3, 5, 10, 15, 30, 60, 120]


def charger_bids_par_seconde(symbole: str, jours: int) -> dict[int, float]:
    """Dernier bid connu pour chaque seconde, indexé par epoch.

    On prend le dernier tick de la seconde, comme le ferait une exécution qui
    regarde le prix à cet instant. Les secondes sans tick restent absentes :
    elles sont sautées plus bas, jamais interpolées — inventer un prix
    fabriquerait un mouvement qui n'a pas eu lieu.
    """
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 init KO : {mt5.last_error()}")
    try:
        if not mt5.symbol_select(symbole, True):
            raise SystemExit(f"Symbole {symbole} introuvable.")
        point = mt5.symbol_info(symbole).point
        fin = dt.datetime.now(dt.timezone.utc)
        debut = fin - dt.timedelta(days=jours)
        ticks = mt5.copy_ticks_range(symbole, debut, fin, mt5.COPY_TICKS_INFO)
    finally:
        mt5.shutdown()

    if ticks is None or len(ticks) == 0:
        raise SystemExit("Aucun tick récupéré.")

    par_seconde: dict[int, float] = {}
    for t in ticks:
        bid = float(t["bid"])
        if bid > 0:
            par_seconde[int(t["time"])] = bid
    return par_seconde, point, len(ticks)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument(
        "--spread", type=float, default=12.0,
        help="spread en points, pour chiffrer la part du risque (mesuré : 12)",
    )
    args = ap.parse_args()

    par_seconde, point, n_ticks = charger_bids_par_seconde(
        args.symbol, args.days
    )
    print(
        f"{n_ticks:,} ticks → {len(par_seconde):,} secondes cotées "
        f"sur {args.days} jours.\n"
    )

    print(
        f"{'horizon':>8}{'paires':>12}{'|Δ| médian':>12}{'90e c.':>9}"
        f"{'frais si stop = |Δ| médian':>29}"
    )
    print("-" * 70)

    lignes = []
    for h in HORIZONS_S:
        ecarts = []
        for seconde, prix in par_seconde.items():
            suivant = par_seconde.get(seconde + h)
            if suivant is not None:
                ecarts.append(abs(suivant - prix) / point)
        if len(ecarts) < 1000:
            print(f"{h:>7}s{'trop peu de paires':>12}")
            continue
        ecarts.sort()
        median = statistics.median(ecarts)
        p90 = ecarts[int(0.9 * (len(ecarts) - 1))]
        part = args.spread / median if median > 0 else float("inf")
        verdict = "  ← finançable" if part <= 0.30 else ""
        print(
            f"{h:>7}s{len(ecarts):>12,}{median:>12.0f}{p90:>9.0f}"
            f"{part:>28.0%}{verdict}"
        )
        lignes.append((h, median, part))

    print(
        f"\nLecture : « frais » = spread {args.spread:.0f} pts rapporté au stop.\n"
        "Le projet plafonne à 30 % du risque. Au-dessus, le trade est refusé."
    )

    finançables = [(h, m) for h, m, p in lignes if p <= 0.30]
    if finançables:
        h, m = finançables[0]
        print(
            f"\nHorizon le plus court finançable : {h} s, stop ≈ {m:.0f} points.\n"
            "C'est le plancher réel du scalping sur cet instrument à ce spread."
        )
    else:
        print(
            "\nAucun horizon sous 2 minutes ne tient sous 30 % de frais.\n"
            "Le spread reste le mur, même à 12 points."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
