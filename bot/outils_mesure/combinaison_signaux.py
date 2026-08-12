"""Les signaux faibles s'additionnent-ils, ou disent-ils la même chose ?

Trois effets réels ont été mesurés, chacun de 2 à 4 points quand le spread en
coûte 13 : le retour à la moyenne du prix de l'or, l'avance de l'argent sur
l'or, et le déséquilibre du côté qui se repositionne dans le flux de cotations.
Aucun ne suffit seul.

Ils viennent de sources différentes — la forme du prix, un autre instrument, le
rythme du flux — donc ils pourraient être partiellement indépendants. C'est la
dernière voie arithmétique : non pas trouver un meilleur signal, mais vérifier
si trois faibles s'additionnent. S'ils sont redondants, la piste se ferme ici.

**Combiner invite le surajustement**, donc deux précautions strictes :
- **poids égaux**, jamais ajustés. Optimiser des poids sur l'étude produirait un
  score flatteur qui ne survivrait pas au contrôle ;
- **normalisation calculée sur l'étude seule**, puis appliquée telle quelle au
  contrôle. La recalculer sur le contrôle y ferait entrer de l'information
  future.

Chaque prédicteur est orienté pour que **positif = l'or devrait monter**.
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
from collections import defaultdict

FLAG_BID, FLAG_ASK = 2, 4


def _serie(mt5, symbole, debut, fin, avec_flux=False):
    ticks = mt5.copy_ticks_range(symbole, debut, fin, mt5.COPY_TICKS_ALL)
    bid, spread = {}, {}
    n_ticks, bid_seul, ask_seul = defaultdict(int), defaultdict(int), defaultdict(int)
    if ticks is None:
        return bid, spread, n_ticks, bid_seul, ask_seul
    for t in ticks:
        b, a = float(t["bid"]), float(t["ask"])
        if b <= 0 or a < b:
            continue
        s = int(t["time"])
        bid[s], spread[s] = b, a - b
        if avec_flux:
            n_ticks[s] += 1
            f = int(t["flags"])
            tb, ta = bool(f & FLAG_BID), bool(f & FLAG_ASK)
            if tb and not ta:
                bid_seul[s] += 1
            elif ta and not tb:
                ask_seul[s] += 1
    return bid, spread, n_ticks, bid_seul, ask_seul


def collecter(or_, argent, jours, horizon, memoire):
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
        obs, curseur = [], debut
        while curseur < fin:
            borne = min(curseur + dt.timedelta(days=5), fin)
            recul = dt.timedelta(seconds=memoire)
            b_or, s_or, n_t, b_s, a_s = _serie(
                mt5, or_, curseur - recul, borne, avec_flux=True
            )
            b_ag, _, _, _, _ = _serie(mt5, argent, curseur - recul, borne)
            if b_or and b_ag:
                obs.extend(
                    _tranche(b_or, s_or, b_ag, n_t, b_s, a_s, pt_or, pt_ag,
                             horizon, memoire, int(curseur.timestamp()))
                )
            curseur = borne
    finally:
        mt5.shutdown()
    return obs


def _tranche(b_or, s_or, b_ag, n_t, b_s, a_s, pt_or, pt_ag, horizon, memoire,
             debut_valide):
    communs = sorted(s for s in b_or if s in b_ag and s >= debut_valide)
    if len(communs) < 100:
        return []
    e_or, e_ag = [], []
    for s in communs[::10]:
        if s - memoire in b_or and s - memoire in b_ag:
            e_or.append((b_or[s] - b_or[s - memoire]) / pt_or)
            e_ag.append((b_ag[s] - b_ag[s - memoire]) / pt_ag)
    if len(e_or) < 30:
        return []
    sd_or = statistics.stdev(e_or) or 1.0
    sd_ag = statistics.stdev(e_ag) or 1.0

    sortie, libre = [], 0
    for t0 in communs:
        if t0 < libre:
            continue
        t1, tp = t0 + horizon, t0 - memoire
        if t1 not in b_or or tp not in b_or or tp not in b_ag:
            continue
        fenetre = range(tp, t0)
        bb = sum(b_s.get(s, 0) for s in fenetre)
        aa = sum(a_s.get(s, 0) for s in fenetre)
        if bb + aa < 5:
            continue
        mom_or = (b_or[t0] - b_or[tp]) / pt_or / sd_or
        mom_ag = (b_ag[t0] - b_ag[tp]) / pt_ag / sd_ag
        sortie.append(
            {
                "t": t0,
                "r": (b_or[t1] - b_or[t0]) / pt_or,
                "spread": s_or.get(t0, 0.0) / pt_or,
                # Orientés : positif = l'or devrait monter.
                "retour": -mom_or,                 # l'or a baissé → il remonte
                "argent": mom_ag - mom_or,         # l'argent a pris de l'avance
                "flux": (bb - aa) / (bb + aa),     # le bid se repositionne seul
            }
        )
        libre = t1
    return sortie


PREDICTEURS = ("retour", "argent", "flux")
NOMS = {
    "retour": "retour à la moyenne",
    "argent": "avance de l'argent",
    "flux": "côté qui se repositionne",
}


def correlation(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    return num / (da * db) if da and db else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--horizon", type=int, default=30)
    ap.add_argument("--memoire", type=int, default=30)
    ap.add_argument("--split", type=float, default=0.6)
    args = ap.parse_args()

    print(f"Collecte, {args.days} j, horizon {args.horizon} s.")
    obs = collecter("XAUUSD", "XAGUSD", args.days, args.horizon, args.memoire)
    if len(obs) < 500:
        raise SystemExit(f"Trop peu d'observations ({len(obs)}).")
    obs.sort(key=lambda o: o["t"])
    coupe = int(len(obs) * args.split)
    etude, controle = obs[:coupe], obs[coupe:]
    print(f"{len(obs):,} observations — étude {len(etude):,}, "
          f"contrôle {len(controle):,}.\n")

    # ── Corrélations : disent-ils la même chose ? ────────────────────────
    print("── Corrélation entre prédicteurs (période d'étude) ──")
    print(f"{'':<26}" + "".join(f"{NOMS[p][:14]:>16}" for p in PREDICTEURS))
    for p in PREDICTEURS:
        ligne = f"{NOMS[p]:<26}"
        for q in PREDICTEURS:
            r = correlation([o[p] for o in etude], [o[q] for o in etude])
            ligne += f"{r:>16.3f}"
        print(ligne)
    print(
        "\nProche de 0 : sources indépendantes, les avantages peuvent "
        "s'additionner.\nProche de ±1 : redondants, combiner n'apporte rien."
    )

    # ── Normalisation sur l'étude seule ─────────────────────────────────
    stats = {
        p: (statistics.fmean(o[p] for o in etude),
            statistics.stdev(o[p] for o in etude) or 1.0)
        for p in PREDICTEURS
    }

    def score(o):
        return sum((o[p] - stats[p][0]) / stats[p][1] for p in PREDICTEURS)

    globales = {
        "étude": statistics.fmean(o["r"] for o in etude),
        "contrôle": statistics.fmean(o["r"] for o in controle),
    }
    frais = statistics.fmean(o["spread"] for o in etude)

    # ── Chaque prédicteur seul, puis le score combiné ────────────────────
    print(f"\n── Avantage du meilleur quintile, en points ──")
    print(f"{'signal':<28}{'étude':>10}{'contrôle':>11}{'frais':>8}")
    print("-" * 58)
    for p in list(PREDICTEURS) + ["combiné"]:
        cle = (lambda o: score(o)) if p == "combiné" else (lambda o, p=p: o[p])
        ligne = f"{(NOMS.get(p) or 'SCORE COMBINÉ'):<28}"
        for nom, ech in (("étude", etude), ("contrôle", controle)):
            tri = sorted(ech, key=cle)
            k = max(30, len(tri) // 5)
            haut = statistics.fmean(o["r"] for o in tri[-k:]) - globales[nom]
            ligne += f"{haut:>+9.1f}p"
            if nom == "étude":
                ligne += " "
        print(ligne + f"{frais:>8.0f}")

    print(
        f"\nLecture : chaque ligne est l'avantage d'une position longue prise\n"
        f"sur le cinquième le plus favorable, à comparer aux {frais:.0f} points\n"
        "de spread qu'elle paierait. Le score combiné est la somme des trois\n"
        "prédicteurs normalisés, à poids égaux — jamais ajustés."
    )

    # ── Le score combiné se renforce-t-il dans sa queue ? ────────────────
    # Seul le contrôle tranche : découper plus fin sur l'étude fabrique des
    # chiffres flatteurs, comme la queue à 1 % de la mesure argent/or, qui
    # donnait +15,3 points en étude et +5,8 hors échantillon.
    print("\n── Queue du score combiné ──")
    print(f"{'queue':<16}{'n étude':>9}{'étude':>9}  │{'n contrôle':>11}"
          f"{'contrôle':>10}")
    print("-" * 58)
    for part, nom in ((0.20, "20 % meilleurs"), (0.05, "5 % meilleurs"),
                      (0.01, "1 % meilleurs")):
        ligne = [nom]
        for cle_nom, ech in (("étude", etude), ("contrôle", controle)):
            tri = sorted(ech, key=score)
            k = max(30, int(len(tri) * part))
            ligne += [k, statistics.fmean(o["r"] for o in tri[-k:])
                      - globales[cle_nom]]
        print(
            f"{ligne[0]:<16}{ligne[1]:>9,}{ligne[2]:>+8.1f}p  │"
            f"{ligne[3]:>11,}{ligne[4]:>+9.1f}p"
        )
    print(
        f"\nIl faudrait dépasser {frais:.0f} points **sur le contrôle** pour "
        "qu'une\nposition couvre seulement son spread, avant tout gain."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
