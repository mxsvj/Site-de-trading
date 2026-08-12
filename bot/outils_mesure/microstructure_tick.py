"""Le rythme du flux de cotations dit-il quelque chose que le prix ne dit pas ?

Sept familles de motifs de prix ont échoué, à quatre échelles. Ce balayage ne
regarde pas la forme du prix mais **le flux qui le produit** : à quelle cadence
les cotations arrivent, quel côté se repositionne, comment le spread respire.
Les bougies détruisent tout cela par construction.

Vérifié le 2026-08-12 avant de bâtir : les ticks de l'or ne portent **ni sens de
transaction ni volume** — pas de drapeau BUY/SELL, `volume` nul sur 506 357
ticks. Il ne reste donc que le flux de cotation. Mais il en reste deux choses :

- **les mises à jour unilatérales** — 6,3 % des ticks ne bougent que l'ask,
  6,0 % que le bid. Quel côté se repositionne seul est une information que le
  prix médian ne contient pas ;
- **`time_msc`** — l'horodatage à la milliseconde, donc la cadence réelle et
  son accélération.

Garde-fous, tous payés par une erreur antérieure du projet :
fenêtres disjointes, conditions strictement antérieures à la décision, excès
mesuré sur la moyenne inconditionnelle (sans quoi la dérive de l'or se fait
passer pour un signal), validation hors échantillon, et seuil de frais lu dans
le spread réel de chaque cellule.
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
from collections import defaultdict
from statistics import NormalDist

ALPHA = 0.05
MIN_CELLULE = 30
FLAG_BID, FLAG_ASK = 2, 4


def _quintile(valeur: float, bornes: list[float]) -> int:
    for i, borne in enumerate(bornes):
        if valeur <= borne:
            return i + 1
    return len(bornes) + 1


def collecter(symbole: str, jours: int, horizon: int, memoire: int):
    """Observations disjointes, par tranches, sans garder tous les ticks."""
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 init KO : {mt5.last_error()}")
    try:
        mt5.symbol_select(symbole, True)
        point = mt5.symbol_info(symbole).point
        fin = dt.datetime.now(dt.timezone.utc)
        debut = fin - dt.timedelta(days=jours)
        observations = []
        curseur = debut
        pas = dt.timedelta(days=5)
        while curseur < fin:
            borne = min(curseur + pas, fin)
            ticks = mt5.copy_ticks_range(
                symbole,
                curseur - dt.timedelta(seconds=memoire),
                borne,
                mt5.COPY_TICKS_ALL,
            )
            if ticks is not None and len(ticks):
                observations.extend(
                    _tranche(ticks, point, horizon, memoire,
                             int(curseur.timestamp()))
                )
            curseur = borne
    finally:
        mt5.shutdown()
    return observations, point


def _tranche(ticks, point, horizon, memoire, debut_valide):
    """Réduit une tranche de ticks en observations de flux."""
    # Index par seconde : dernier prix connu, et les compteurs de flux.
    dernier: dict[int, float] = {}
    spread_s: dict[int, float] = {}
    n_ticks: dict[int, int] = defaultdict(int)
    bid_seul: dict[int, int] = defaultdict(int)
    ask_seul: dict[int, int] = defaultdict(int)

    for t in ticks:
        b, a = float(t["bid"]), float(t["ask"])
        if b <= 0 or a < b:
            continue
        s = int(t["time"])
        dernier[s] = b
        spread_s[s] = (a - b) / point
        n_ticks[s] += 1
        f = int(t["flags"])
        touche_bid, touche_ask = bool(f & FLAG_BID), bool(f & FLAG_ASK)
        if touche_bid and not touche_ask:
            bid_seul[s] += 1
        elif touche_ask and not touche_bid:
            ask_seul[s] += 1

    sortie = []
    secondes = sorted(s for s in dernier if s >= debut_valide)
    libre = 0
    for t0 in secondes:
        if t0 < libre:
            continue
        t1 = t0 + horizon
        if t1 not in dernier:
            continue
        fenetre = range(t0 - memoire, t0)          # strictement antérieure
        recent = range(t0 - memoire // 4, t0)
        total = sum(n_ticks.get(s, 0) for s in fenetre)
        if total < 20:
            continue

        b_s = sum(bid_seul.get(s, 0) for s in fenetre)
        a_s = sum(ask_seul.get(s, 0) for s in fenetre)
        unilateral = (b_s + a_s) or 1
        cadence = total / memoire
        cadence_recente = sum(n_ticks.get(s, 0) for s in recent) / (memoire // 4)
        spreads = [spread_s[s] for s in fenetre if s in spread_s]
        if not spreads:
            continue

        sortie.append(
            {
                "t": t0,
                "r": (dernier[t1] - dernier[t0]) / point,
                "spread": spread_s[t0],
                # Quel côté s'est repositionné seul : +1 = bid seul domine.
                "desequilibre": (b_s - a_s) / unilateral,
                "cadence": cadence,
                # >1 : le flux accélère.
                "acceleration": cadence_recente / cadence if cadence > 0 else 1.0,
                # Le spread se creuse-t-il par rapport à sa moyenne récente ?
                "respiration": spread_s[t0] / statistics.fmean(spreads)
                if statistics.fmean(spreads) > 0 else 1.0,
            }
        )
        libre = t1
    return sortie


def cellules(obs: list[dict]) -> dict[tuple[str, str], list]:
    cles = ("desequilibre", "cadence", "acceleration", "respiration")
    bornes = {}
    for cle in cles:
        v = sorted(o[cle] for o in obs)
        bornes[cle] = [v[int(q * (len(v) - 1))] for q in (0.2, 0.4, 0.6, 0.8)]

    noms = {
        "desequilibre": "côté qui bouge",
        "cadence": "cadence du flux",
        "acceleration": "accélération",
        "respiration": "spread relatif",
    }
    groupes: dict[tuple[str, str], list] = defaultdict(list)
    for o in obs:
        for cle, nom in noms.items():
            groupes[(nom, f"Q{_quintile(o[cle], bornes[cle])}")].append(o)
        groupes[
            ("côté qui bouge", "bid seul" if o["desequilibre"] > 0 else "ask seul")
        ].append(o)
    return groupes


def juger(groupes, globale: float):
    lignes = []
    for (critere, valeur), items in groupes.items():
        n = len(items)
        if n < MIN_CELLULE:
            continue
        r = [o["r"] for o in items]
        moyenne = statistics.fmean(r)
        if n < 2:
            continue
        ecart = statistics.stdev(r)
        if ecart <= 0:
            continue
        exces = moyenne - globale
        lignes.append(
            {
                "critere": critere,
                "valeur": valeur,
                "n": n,
                "exces": exces,
                "t": exces / (ecart / n**0.5),
                "frais": statistics.fmean(o["spread"] for o in items),
            }
        )
    lignes.sort(key=lambda c: -abs(c["t"]))
    return lignes


def puissance(groupes, globale: float, seuil: float) -> tuple[float, float]:
    """Plus petit effet que ce balayage aurait pu déclarer significatif.

    Sans ce contrôle, « rien trouvé » peut vouloir dire « rien à trouver » ou
    « pas assez d'observations » — deux conclusions opposées. Calculé sur la
    cellule la mieux fournie, donc la plus favorable, et comparé au spread moyen
    qu'il faudrait couvrir.
    """
    meilleure, taille = None, 0
    for items in groupes.values():
        if len(items) > taille and len(items) > 1:
            meilleure, taille = items, len(items)
    if meilleure is None:
        return float("inf"), float("inf")
    ecart = statistics.stdev(o["r"] for o in meilleure)
    detectable = seuil * ecart / taille**0.5
    rentable = statistics.fmean(o["spread"] for o in meilleure)
    return detectable, rentable


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--horizon", type=int, default=30, help="secondes")
    ap.add_argument("--memoire", type=int, default=60, help="secondes observées")
    ap.add_argument("--split", type=float, default=0.6)
    args = ap.parse_args()

    print(
        f"Collecte {args.symbol}, {args.days} j, horizon {args.horizon} s, "
        f"mémoire {args.memoire} s."
    )
    obs, _ = collecter(args.symbol, args.days, args.horizon, args.memoire)
    if len(obs) < 500:
        raise SystemExit(f"Trop peu d'observations ({len(obs)}).")
    obs.sort(key=lambda o: o["t"])

    coupe = int(len(obs) * args.split)
    etude, controle = obs[:coupe], obs[coupe:]
    g_e = statistics.fmean(o["r"] for o in etude)
    g_c = statistics.fmean(o["r"] for o in controle)
    print(
        f"\n{len(obs):,} observations disjointes.\n"
        f"Étude {len(etude):,}, contrôle {len(controle):,}.\n"
        f"Dérive inconditionnelle : {g_e:+.2f} pts en étude, "
        f"{g_c:+.2f} en contrôle — retirée des deux côtés."
    )

    le = juger(cellules(etude), g_e)
    lc = {(c["critere"], c["valeur"]): c for c in juger(cellules(controle), g_c)}
    if not le:
        raise SystemExit("Aucune cellule assez fournie.")
    seuil = NormalDist().inv_cdf(1.0 - ALPHA / (2.0 * len(le)))
    print(f"{len(le)} cellules, seuil de t {seuil:.2f} (Bonferroni).\n")

    print(
        f"{'critère':<18}{'valeur':<10}{'n':>7}{'excès':>9}{'t':>7}{'frais':>7}"
        f"  │{'excès':>9}{'t':>7}"
    )
    print("-" * 76)
    for c in le[:14]:
        h = lc.get((c["critere"], c["valeur"]))
        marque = "*" if abs(c["t"]) > seuil else " "
        if h and c["exces"] * h["exces"] < 0:
            marque = "!"
        d = f"{h['exces']:>+8.1f}p{h['t']:>7.2f}" if h else f"{'—':>16}"
        print(
            f"{marque}{c['critere']:<17}{c['valeur']:<10}{c['n']:>7}"
            f"{c['exces']:>+8.1f}p{c['t']:>7.2f}{c['frais']:>7.0f}  │{d}"
        )

    detectable, rentable = puissance(cellules(etude), g_e, seuil)
    print("\n── Puissance de ce balayage ───────────────────────")
    print(f"Plus petit effet détectable       : {detectable:.1f} points")
    print(f"Plus petit effet rentable         : {rentable:.1f} points (spread)")
    if detectable <= rentable:
        print(
            "\nLa détection est plus fine que le seuil de rentabilité.\n"
            "Un avantage exploitable aurait été vu : ce balayage conclut."
        )
    else:
        print(
            "\nLa détection est plus grossière que le seuil de rentabilité :\n"
            f"il faudrait environ {(detectable / rentable) ** 2:.1f} fois plus\n"
            "d'observations. Ce balayage ne conclut rien."
        )

    survivants = [
        c for c in le
        if abs(c["t"]) > seuil
        and (h := lc.get((c["critere"], c["valeur"])))
        and abs(h["t"]) > seuil
        and c["exces"] * h["exces"] > 0
    ]
    print("\n── Verdict ────────────────────────────────────────")
    if not survivants:
        print(
            "Aucune condition de flux ne franchit le seuil des deux côtés.\n"
            "Le rythme des cotations ne porte pas plus que leur forme."
        )
        return 0
    for c in survivants:
        couvre = abs(c["exces"]) > c["frais"]
        print(
            f"  {c['critere']} = {c['valeur']} : {c['exces']:+.1f} points, "
            f"t = {c['t']:.2f}, frais {c['frais']:.0f} — "
            f"{'COUVRE ses frais' if couvre else 'ne couvre pas'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
