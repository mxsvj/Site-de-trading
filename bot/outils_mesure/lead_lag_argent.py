"""L'argent précède-t-il l'or à l'échelle de la seconde ?

Dernière source qui ne soit pas une forme de plus sur le prix de l'or : le prix
d'un **autre** instrument. On continue de ne trader que XAUUSD — l'argent sert
d'entrée, jamais de position, exactement comme EUR/USD dans `lead-lag`.

`lead-lag` avait échoué avec EUR/USD (−0,178 R hors échantillon), mais EUR/USD
est un mauvais candidat : le lien est macro et lent. L'argent est le substitut
direct de l'or, coté à la même densité (3,2 contre 3,3 ticks/s, mesuré le
2026-08-12), et l'écart or/argent n'a jamais été mesuré ici.

Hypothèse testée : quand l'argent s'écarte de l'or, l'or rattrape. La variable
est la **divergence** — le mouvement de l'argent moins celui de l'or, chacun
rapporté à sa propre volatilité, sans quoi on comparerait des points d'or à des
points d'argent qui n'ont ni la même taille ni la même agitation.

Garde-fous habituels : fenêtres disjointes, conditions strictement antérieures à
la décision, excès sur la moyenne inconditionnelle, validation hors échantillon,
contrôle de puissance, et seuil de frais lu dans le spread réel de **l'or**,
puisque c'est lui qu'on paierait.

Témoin indispensable : le momentum de l'or seul. Si la divergence ne fait pas
mieux que lui, elle n'apporte rien que l'or ne contienne déjà.
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
from collections import defaultdict
from statistics import NormalDist

ALPHA = 0.05
MIN_CELLULE = 30


def _quintile(v: float, bornes: list[float]) -> int:
    for i, b in enumerate(bornes):
        if v <= b:
            return i + 1
    return len(bornes) + 1


def _serie(mt5, symbole: str, debut, fin) -> tuple[dict[int, float], dict[int, float]]:
    """Dernier bid et spread par seconde."""
    ticks = mt5.copy_ticks_range(symbole, debut, fin, mt5.COPY_TICKS_ALL)
    bid: dict[int, float] = {}
    spread: dict[int, float] = {}
    if ticks is None:
        return bid, spread
    for t in ticks:
        b, a = float(t["bid"]), float(t["ask"])
        if b > 0 and a >= b:
            s = int(t["time"])
            bid[s] = b
            spread[s] = a - b
    return bid, spread


def collecter(or_: str, argent: str, jours: int, horizon: int, memoire: int):
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 init KO : {mt5.last_error()}")
    try:
        for s in (or_, argent):
            if not mt5.symbol_select(s, True):
                raise SystemExit(f"Symbole {s} introuvable.")
        pt_or = mt5.symbol_info(or_).point
        pt_ag = mt5.symbol_info(argent).point

        fin = dt.datetime.now(dt.timezone.utc)
        debut = fin - dt.timedelta(days=jours)
        obs = []
        curseur = debut
        pas = dt.timedelta(days=5)
        while curseur < fin:
            borne = min(curseur + pas, fin)
            recul = dt.timedelta(seconds=memoire)
            b_or, s_or = _serie(mt5, or_, curseur - recul, borne)
            b_ag, _ = _serie(mt5, argent, curseur - recul, borne)
            if b_or and b_ag:
                obs.extend(
                    _tranche(
                        b_or, s_or, b_ag, pt_or, pt_ag, horizon, memoire,
                        int(curseur.timestamp()),
                    )
                )
            curseur = borne
    finally:
        mt5.shutdown()
    return obs


def _tranche(b_or, s_or, b_ag, pt_or, pt_ag, horizon, memoire, debut_valide):
    """Observations disjointes où les deux instruments cotent."""
    communs = sorted(s for s in b_or if s in b_ag and s >= debut_valide)
    if len(communs) < 100:
        return []

    # Échelle propre à chaque métal : un point d'or et un point d'argent n'ont
    # ni la même taille ni la même agitation. On normalise par l'écart-type des
    # rendements sur la mémoire, estimé sur cette tranche.
    ech_or, ech_ag = [], []
    for s in communs[::10]:
        if s - memoire in b_or and s - memoire in b_ag:
            ech_or.append((b_or[s] - b_or[s - memoire]) / pt_or)
            ech_ag.append((b_ag[s] - b_ag[s - memoire]) / pt_ag)
    if len(ech_or) < 30:
        return []
    sd_or = statistics.stdev(ech_or) or 1.0
    sd_ag = statistics.stdev(ech_ag) or 1.0

    sortie = []
    libre = 0
    for t0 in communs:
        if t0 < libre:
            continue
        t1, tp = t0 + horizon, t0 - memoire
        if t1 not in b_or or tp not in b_or or tp not in b_ag:
            continue
        mom_or = (b_or[t0] - b_or[tp]) / pt_or / sd_or
        mom_ag = (b_ag[t0] - b_ag[tp]) / pt_ag / sd_ag
        sortie.append(
            {
                "t": t0,
                "r": (b_or[t1] - b_or[t0]) / pt_or,
                "spread": s_or.get(t0, 0.0) / pt_or,
                "divergence": mom_ag - mom_or,   # l'argent a-t-il pris de l'avance
                "mom_or": mom_or,                # témoin : l'or seul
                "mom_ag": mom_ag,
            }
        )
        libre = t1
    return sortie


def cellules(obs):
    noms = {
        "divergence": "avance de l'argent",
        "mom_or": "momentum de l'or",
        "mom_ag": "momentum de l'argent",
    }
    bornes = {}
    for cle in noms:
        v = sorted(o[cle] for o in obs)
        bornes[cle] = [v[int(q * (len(v) - 1))] for q in (0.2, 0.4, 0.6, 0.8)]
    groupes = defaultdict(list)
    for o in obs:
        for cle, nom in noms.items():
            groupes[(nom, f"Q{_quintile(o[cle], bornes[cle])}")].append(o)
    return groupes


def juger(groupes, globale):
    lignes = []
    for (critere, valeur), items in groupes.items():
        n = len(items)
        if n < MIN_CELLULE:
            continue
        r = [o["r"] for o in items]
        if n < 2:
            continue
        ecart = statistics.stdev(r)
        if ecart <= 0:
            continue
        exces = statistics.fmean(r) - globale
        lignes.append(
            {
                "critere": critere, "valeur": valeur, "n": n, "exces": exces,
                "t": exces / (ecart / n**0.5),
                "frais": statistics.fmean(o["spread"] for o in items),
                "ecart": ecart,
            }
        )
    lignes.sort(key=lambda c: -abs(c["t"]))
    return lignes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--or-symbol", default="XAUUSD")
    ap.add_argument("--argent", default="XAGUSD")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--horizon", type=int, default=30, help="secondes")
    ap.add_argument("--memoire", type=int, default=30, help="secondes")
    ap.add_argument("--split", type=float, default=0.6)
    args = ap.parse_args()

    print(
        f"Collecte {args.or_symbol} et {args.argent}, {args.days} j, "
        f"horizon {args.horizon} s, mémoire {args.memoire} s."
    )
    obs = collecter(
        args.or_symbol, args.argent, args.days, args.horizon, args.memoire
    )
    if len(obs) < 500:
        raise SystemExit(f"Trop peu d'observations ({len(obs)}).")
    obs.sort(key=lambda o: o["t"])

    coupe = int(len(obs) * args.split)
    etude, controle = obs[:coupe], obs[coupe:]
    g_e = statistics.fmean(o["r"] for o in etude)
    g_c = statistics.fmean(o["r"] for o in controle)
    print(
        f"\n{len(obs):,} observations disjointes où les deux métaux cotent.\n"
        f"Étude {len(etude):,}, contrôle {len(controle):,}.\n"
        f"Dérive de l'or : {g_e:+.2f} pts en étude, {g_c:+.2f} en contrôle "
        "— retirée des deux côtés."
    )

    le = juger(cellules(etude), g_e)
    lc = {(c["critere"], c["valeur"]): c for c in juger(cellules(controle), g_c)}
    seuil = NormalDist().inv_cdf(1.0 - ALPHA / (2.0 * len(le)))
    print(f"{len(le)} cellules, seuil de t {seuil:.2f} (Bonferroni).\n")

    print(
        f"{'critère':<22}{'valeur':<8}{'n':>7}{'excès':>9}{'t':>7}{'frais':>7}"
        f"  │{'excès':>9}{'t':>7}"
    )
    print("-" * 78)
    for c in le:
        h = lc.get((c["critere"], c["valeur"]))
        marque = "*" if abs(c["t"]) > seuil else " "
        if h and c["exces"] * h["exces"] < 0:
            marque = "!"
        d = f"{h['exces']:>+8.1f}p{h['t']:>7.2f}" if h else f"{'—':>16}"
        print(
            f"{marque}{c['critere']:<21}{c['valeur']:<8}{c['n']:>7}"
            f"{c['exces']:>+8.1f}p{c['t']:>7.2f}{c['frais']:>7.0f}  │{d}"
        )

    meilleure = max(le, key=lambda c: c["n"])
    detectable = seuil * meilleure["ecart"] / meilleure["n"] ** 0.5
    rentable = statistics.fmean(o["spread"] for o in etude)
    print("\n── Puissance ──────────────────────────────────────")
    print(f"Plus petit effet détectable       : {detectable:.1f} points")
    print(f"Plus petit effet rentable         : {rentable:.1f} points")
    print(
        "\nLa détection est plus fine que le seuil : un avantage exploitable\n"
        "aurait été vu."
        if detectable <= rentable
        else f"\nDétection trop grossière : il faudrait "
        f"{(detectable / rentable) ** 2:.1f} fois plus d'observations."
    )

    # ── L'effet grandit-il dans la queue ? ───────────────────────────────
    # Les quintiles moyennent l'extrême avec le tiède. Si le signal est réel,
    # il doit croître avec l'ampleur de la divergence. C'est une hypothèse
    # nouvelle, donc lue sur le contrôle : découper plus fin invite la
    # sélection, et l'étude seule ne prouverait rien.
    print("\n── L'effet croît-il avec l'ampleur de la divergence ? ──")
    print(
        f"{'queue':<15}{'n':>7}{'achat':>8}{'vente':>8}  │"
        f"{'n':>7}{'achat':>8}{'vente':>8}"
    )
    print(f"{'':<15}{'— étude —':>23}  │{'— contrôle —':>23}")
    print("-" * 64)
    for part, nom in ((0.20, "20 % extrêmes"), (0.05, "5 % extrêmes"),
                      (0.01, "1 % extrêmes")):
        ligne = [nom]
        for ech, globale in ((etude, g_e), (controle, g_c)):
            tri = sorted(ech, key=lambda o: o["divergence"])
            k = max(MIN_CELLULE, int(len(tri) * part))
            # Chaque côté est un trade distinct qui paie son propre spread :
            # les additionner ferait croire à un avantage deux fois plus grand
            # que celui qu'une position capte réellement.
            haut = statistics.fmean(o["r"] for o in tri[-k:]) - globale
            bas = -(statistics.fmean(o["r"] for o in tri[:k]) - globale)
            ligne += [k, haut, bas]
        print(
            f"{ligne[0]:<15}{ligne[1]:>7,}{ligne[2]:>+7.1f}p{ligne[3]:>+7.1f}p"
            f"  │{ligne[4]:>7,}{ligne[5]:>+7.1f}p{ligne[6]:>+7.1f}p"
        )
    print(
        f"\n« achat » = avantage d'une position longue sur la queue haute,\n"
        f"« vente » = avantage d'une position courte sur la queue basse.\n"
        f"Chacune est un trade séparé qui paie ses {rentable:.0f} points de\n"
        "spread : c'est à ce chiffre-là qu'il faut les comparer, pas à leur somme."
    )

    survivants = [
        c for c in le
        if abs(c["t"]) > seuil
        and (h := lc.get((c["critere"], c["valeur"])))
        and abs(h["t"]) > seuil and c["exces"] * h["exces"] > 0
    ]
    print("\n── Verdict ────────────────────────────────────────")
    if not survivants:
        print(
            "Aucune condition ne franchit le seuil des deux côtés.\n"
            "L'argent ne précède pas l'or à cette échelle."
        )
        return 0
    for c in survivants:
        couvre = abs(c["exces"]) > c["frais"]
        print(
            f"  {c['critere']} = {c['valeur']} : {c['exces']:+.1f} pts, "
            f"t = {c['t']:.2f}, frais {c['frais']:.0f} — "
            f"{'COUVRE ses frais' if couvre else 'ne couvre pas'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
