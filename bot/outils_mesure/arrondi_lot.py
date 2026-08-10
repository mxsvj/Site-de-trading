"""Ce que l'arrondi du lot coûte vraiment, et où sont les stops efficaces.

Le volume vaut `risque / (stop × valeur du point)`, puis il est tronqué au pas
du courtier (`risk.py`, `math.floor`). Sur un compte de 1 000 € le volume tourne
autour de 0,01 à 0,10 lot : la troncature y pèse énormément.

Le point aveugle est ailleurs. `r_multiple()` ne dépend que de distances de prix
— le volume n'y entre pas. Tous les verdicts du projet sont en R, donc **aucun
n'a jamais vu cet effet**, alors qu'il déforme le résultat en euros jusqu'à
moitié.

Ce script ne teste aucune stratégie et ne consomme aucune hypothèse.
"""

from __future__ import annotations

import argparse
import math
import statistics


def volume_tronque(stop_points: float, cible: float, vpp: float, pas: float,
                   lot_min: float) -> float:
    """Reproduit exactement `risk.position_size`, troncature comprise."""
    if stop_points <= 0:
        return 0.0
    exact = cible / (stop_points * vpp)
    lots = math.floor(exact / pas + 1e-9) * pas
    return lots if lots >= lot_min else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--risk", type=float, default=0.5, help="%% du capital")
    ap.add_argument("--vpp", type=float, default=0.8652,
                    help="valeur du point pour 1 lot")
    ap.add_argument("--pas", type=float, default=0.01, help="pas de volume")
    ap.add_argument("--lot-min", type=float, default=0.01)
    args = ap.parse_args()

    cible = args.balance * args.risk / 100.0
    stop_max = cible / (args.lot_min * args.vpp)
    print(
        f"Capital {args.balance:,.0f} €, risque {args.risk} % = {cible:.2f} € "
        f"par trade.\nStop maximal praticable : {stop_max:.0f} points.\n"
    )

    # ── L'échelle des stops efficaces ────────────────────────────────────
    # Le volume exact vaut cible/(stop × vpp). Il tombe pile sur un multiple du
    # pas quand stop = stop_max / k. Là, et seulement là, le risque pris est
    # exactement le risque visé.
    print("── Stops efficaces : ceux où la troncature ne coûte rien ──")
    print(f"{'lots':>8}{'stop exact':>14}{'risque':>10}{'+0,1 pt →':>12}")
    print("-" * 46)
    echelle = []
    for k in range(1, 11):
        s = stop_max / k
        lots = k * args.pas
        echelle.append((s, lots))
        # Juste au-dessus de la valeur exacte, le volume perd une marche.
        juste_au_dessus = volume_tronque(
            s + 0.1, cible, args.vpp, args.pas, args.lot_min
        ) * (s + 0.1) * args.vpp / cible
        print(
            f"{lots:>8.2f}{s:>14.2f}{lots * s * args.vpp:>10.2f} €"
            f"{juste_au_dessus:>11.0%}"
        )
    print(
        "  La dernière colonne est le risque obtenu 0,1 point plus loin : ces\n"
        "  valeurs sont des lames de rasoir, pas des plages."
    )

    # ── Le coût de rater ces valeurs ─────────────────────────────────────
    print("\n── Ce que coûte un stop mal placé ──")
    print(f"{'stop':>8}{'lots':>8}{'risque réel':>13}{'du visé':>10}")
    print("-" * 40)
    for s in (289, 300, 192, 200, 144, 150, 115, 120, 96, 100):
        lots = volume_tronque(s, cible, args.vpp, args.pas, args.lot_min)
        reel = lots * s * args.vpp
        print(f"{s:>8}{lots:>8.2f}{reel:>12.2f} €{reel / cible:>9.0%}")

    # ── Efficacité moyenne sur toute la fenêtre praticable ───────────────
    efficacites = []
    pires = []
    for centieme in range(400, int(stop_max * 10) + 1):  # 40,0 → 578,0 points
        s = centieme / 10.0
        lots = volume_tronque(s, cible, args.vpp, args.pas, args.lot_min)
        if lots <= 0:
            continue
        eff = lots * s * args.vpp / cible
        efficacites.append(eff)
        pires.append((eff, s))
    pires.sort()

    print(
        f"\n── Sur toute la fenêtre praticable (40 à {stop_max:.0f} points) ──"
    )
    print(f"Efficacité moyenne du risque : {statistics.fmean(efficacites):.1%}")
    print(f"Efficacité médiane           : {statistics.median(efficacites):.1%}")
    print(f"Pire cas                     : {pires[0][0]:.1%} "
          f"(stop {pires[0][1]:.1f} points)")
    print(
        f"\nAutrement dit : en moyenne le bot risque "
        f"{statistics.fmean(efficacites):.0%} de ce qu'il annonce, et la perte\n"
        "n'est pas uniforme — elle frappe d'autant plus fort que le stop est\n"
        "large, donc elle pénalise systématiquement les setups à stop ample."
    )

    # ── Le biais est-il systématique selon la largeur du stop ? ──────────
    print("\n── L'arrondi frappe-t-il également tous les stops ? ──")
    print(f"{'tranche de stop':>20}{'efficacité moyenne':>22}")
    print("-" * 42)
    tranches = [(40, 100), (100, 200), (200, 300), (300, 450), (450, 578)]
    for bas, haut in tranches:
        vals = [
            volume_tronque(c / 10.0, cible, args.vpp, args.pas, args.lot_min)
            * (c / 10.0) * args.vpp / cible
            for c in range(bas * 10, haut * 10)
            if volume_tronque(c / 10.0, cible, args.vpp, args.pas, args.lot_min) > 0
        ]
        if vals:
            print(f"{bas:>9}–{haut:<10}{statistics.fmean(vals):>21.1%}")

    print(
        "\nConséquence pour la mesure : deux stratégies de même espérance en R\n"
        "n'ont pas la même espérance en euros si leurs stops diffèrent. Aucun\n"
        "backtest du projet ne pouvait le voir, puisqu'ils concluent tous en R."
    )

    # ── Que récupérerait-on en resserrant légèrement le stop ? ───────────
    # Resserrer fait monter le volume exact et peut franchir une marche. On
    # n'accepte le décalage que s'il reste sous une tolérance : sinon on
    # changerait le trade au lieu d'en corriger le dimensionnement.
    print("\n── Récupération par ajustement toléré du stop ──")
    print(f"{'tolérance':>12}{'efficacité':>13}{'stops ajustés':>16}")
    print("-" * 41)
    for tol in (0.0, 0.005, 0.01, 0.02, 0.05, 0.10):
        effs, touches = [], 0
        for centieme in range(400, int(stop_max * 10) + 1):
            s = centieme / 10.0
            base = volume_tronque(s, cible, args.vpp, args.pas, args.lot_min)
            if base <= 0:
                continue
            exact_marches = cible / (s * args.vpp * args.pas)
            k = math.ceil(exact_marches - 1e-9)
            s_cible = stop_max / k if k > 0 else s
            if s_cible > 0 and (s - s_cible) / s <= tol and s_cible <= s:
                effs.append(k * args.pas * s_cible * args.vpp / cible)
                if s_cible < s:
                    touches += 1
            else:
                effs.append(base * s * args.vpp / cible)
        print(
            f"{tol:>11.1%}{statistics.fmean(effs):>13.1%}"
            f"{touches / len(effs):>15.0%}"
        )
    print(
        "\nCette piste ne mène nulle part, et c'est le résultat utile : ajuster\n"
        "le stop de 2 % ne récupère que 1,2 point sur les 19 perdus, parce que\n"
        "les stops efficaces sont des lames de rasoir, pas des plages. Il\n"
        "faudrait tolérer 10 % de déplacement pour gagner 6 points — à ce\n"
        "niveau on ne corrige plus un arrondi, on change le trade.\n"
        "\nLa cause n'est pas le placement du stop : c'est qu'à 1 000 € de\n"
        "capital le pas de 0,01 lot ne laisse que dix volumes distincts sur\n"
        "toute la fenêtre praticable. Le seul vrai remède est plus de capital,\n"
        "ou un courtier au pas plus fin. À défaut, il faut au moins que les\n"
        "backtests cessent de conclure en R comme si l'arrondi n'existait pas."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
