"""Existe-t-il une asymétrie directionnelle de l'or à 30–60 secondes ?

Question ouverte par deux mesures du 2026-08-10 : le spread réel est de 12
points et non 24, et le mouvement médian à 30 s est de 54 points — donc 22 % de
frais, sous le plafond de 30 %. Cette fenêtre était refusée par le coût, elle ne
l'est plus, et rien ne l'a jamais mesurée. `ScalpXAU` échouait à 1,5 s, un
horizon où le spread vaut 171 % du mouvement médian : perdu d'avance quel que
soit le signal, donc ne conclut rien sur 30 s.

Ce script ne teste aucune stratégie. Il mesure le rendement futur signé sous des
conditions observables à l'instant de la décision, comme `edge-scan` mais sur
des ticks. Les pièges que le projet a déjà payés sont traités explicitement :

- **Fenêtres disjointes.** Deux fenêtres qui se recouvrent partagent du prix et
  gonflent la statistique t d'environ racine(horizon). On avance donc d'un pas
  entier à chaque observation.
- **Rien de futur dans les conditions.** Toute condition est calculée sur des
  secondes strictement antérieures à t0.
- **Chaque cellule sur son propre spread.** Le spread est lu dans le tick à t0
  et moyenné par cellule. Juger une cellule d'heure creuse au spread médian du
  marché flatterait précisément les heures où le coût mord le plus.
- **Contrôle de puissance.** Un résultat nul ne vaut rien tant qu'on ignore ce
  qu'on aurait été capable de voir.
- **Confrontation hors échantillon.** Le verdict se lit sur la période de
  contrôle, jamais sur l'étude.
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
from collections import defaultdict
from statistics import NormalDist

ALPHA = 0.05
MIN_CELLULE = 30


def _quintile(valeur: float, bornes: list[float]) -> int:
    for i, borne in enumerate(bornes):
        if valeur <= borne:
            return i + 1
    return len(bornes) + 1


def collecter(symbole: str, jours: int, horizon: int, tz_shift: float):
    """Extrait des observations disjointes, en streamant les ticks par tranches.

    On ne garde jamais tous les ticks en mémoire : chaque tranche est réduite à
    ses observations (quelques milliers) puis jetée.
    """
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 init KO : {mt5.last_error()}")
    try:
        if not mt5.symbol_select(symbole, True):
            raise SystemExit(f"Symbole {symbole} introuvable.")
        point = mt5.symbol_info(symbole).point
        fin = dt.datetime.now(dt.timezone.utc)
        debut_global = fin - dt.timedelta(days=jours)

        # Mémoire nécessaire au calcul des conditions : on regarde jusqu'à
        # 10 horizons en arrière, donc les tranches se chevauchent d'autant.
        recul = 10 * horizon
        observations = []
        tranche = dt.timedelta(days=5)
        curseur = debut_global
        while curseur < fin:
            borne = min(curseur + tranche, fin)
            ticks = mt5.copy_ticks_range(
                symbole,
                curseur - dt.timedelta(seconds=recul),
                borne,
                mt5.COPY_TICKS_INFO,
            )
            if ticks is not None and len(ticks):
                observations.extend(
                    _observations_tranche(
                        ticks, point, horizon, recul, int(curseur.timestamp())
                    )
                )
            curseur = borne
    finally:
        mt5.shutdown()
    return observations, point


def _observations_tranche(ticks, point, horizon, recul, debut_valide):
    """Réduit une tranche de ticks en observations disjointes."""
    bid: dict[int, float] = {}
    spread: dict[int, float] = {}
    compte: dict[int, int] = defaultdict(int)
    for t in ticks:
        b, a = float(t["bid"]), float(t["ask"])
        if b <= 0 or a < b:
            continue
        s = int(t["time"])
        bid[s] = b
        spread[s] = (a - b) / point
        compte[s] += 1

    if not bid:
        return []

    sortie = []
    secondes = sorted(s for s in bid if s >= debut_valide)
    prochain_libre = 0
    for t0 in secondes:
        if t0 < prochain_libre:
            continue  # fenêtres disjointes : on ne recouvre jamais
        t1 = t0 + horizon
        if t1 not in bid:
            continue
        # Conditions : uniquement des secondes strictement antérieures à t0.
        t_prec = t0 - horizon
        t_vol = t0 - recul
        if t_prec not in bid or t_vol not in bid:
            continue

        rendement = (bid[t1] - bid[t0]) / point
        momentum = (bid[t0] - bid[t_prec]) / point
        fenetre = [bid[s] for s in range(t_vol, t0 + 1) if s in bid]
        if len(fenetre) < 3:
            continue
        volatilite = (max(fenetre) - min(fenetre)) / point
        activite = sum(compte.get(s, 0) for s in range(t_prec, t0 + 1))

        sortie.append(
            {
                "t": t0,
                "r": rendement,
                "mom": momentum,
                "vol": volatilite,
                "spread": spread[t0],
                "act": activite,
            }
        )
        prochain_libre = t1
    return sortie


def cellules(obs: list[dict], tz_shift: float) -> dict[tuple[str, str], list]:
    """Range chaque observation dans ses cellules, conditions observables."""
    if not obs:
        return {}
    bornes = {}
    for cle in ("mom", "vol", "spread", "act"):
        valeurs = sorted(o[cle] for o in obs)
        bornes[cle] = [
            valeurs[int(q * (len(valeurs) - 1))] for q in (0.2, 0.4, 0.6, 0.8)
        ]

    groupes: dict[tuple[str, str], list] = defaultdict(list)
    decalage = dt.timedelta(hours=tz_shift)
    for o in obs:
        moment = dt.datetime.fromtimestamp(o["t"], tz=dt.timezone.utc) + decalage
        groupes[("heure", f"{moment.hour:02d}h")].append(o)
        groupes[("jour", moment.strftime("%a"))].append(o)
        groupes[
            ("momentum", "hausse" if o["mom"] > 0 else "baisse")
        ].append(o)
        for cle, nom in (
            ("mom", "momentum Q"),
            ("vol", "volatilité Q"),
            ("spread", "spread Q"),
            ("act", "activité Q"),
        ):
            groupes[(nom.rstrip(" Q"), f"Q{_quintile(o[cle], bornes[cle])}")].append(o)
    return groupes


def juger(groupes, etiquette, seuil=None):
    """Statistiques par cellule. Le seuil de frais est propre à la cellule."""
    lignes = []
    for (critere, valeur), items in groupes.items():
        n = len(items)
        if n < MIN_CELLULE:
            continue
        rends = [o["r"] for o in items]
        moyenne = statistics.fmean(rends)
        if n < 2:
            continue
        ecart = statistics.stdev(rends)
        if ecart <= 0:
            continue
        t = moyenne / (ecart / n**0.5)
        frais = statistics.fmean(o["spread"] for o in items)
        lignes.append(
            {
                "critere": critere,
                "valeur": valeur,
                "n": n,
                "moyenne": moyenne,
                "ecart": ecart,
                "t": t,
                "frais": frais,
            }
        )
    lignes.sort(key=lambda c: -abs(c["t"]))
    return lignes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--horizon", type=int, default=30, help="secondes")
    ap.add_argument("--split", type=float, default=0.6)
    ap.add_argument("--tz-shift", type=float, default=-3.0)
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    print(
        f"Collecte {args.symbol}, {args.days} jours, horizon {args.horizon} s, "
        f"fenêtres disjointes."
    )
    obs, point = collecter(
        args.symbol, args.days, args.horizon, args.tz_shift
    )
    if len(obs) < 500:
        raise SystemExit(f"Trop peu d'observations ({len(obs)}).")
    obs.sort(key=lambda o: o["t"])

    coupe = int(len(obs) * args.split)
    etude, controle = obs[:coupe], obs[coupe:]
    d0 = dt.datetime.fromtimestamp(obs[0]["t"], tz=dt.timezone.utc)
    dc = dt.datetime.fromtimestamp(etude[-1]["t"], tz=dt.timezone.utc)
    d1 = dt.datetime.fromtimestamp(obs[-1]["t"], tz=dt.timezone.utc)
    print(
        f"\n{len(obs):,} observations disjointes.\n"
        f"Étude    : {len(etude):,} ({d0:%Y-%m-%d} → {dc:%Y-%m-%d})\n"
        f"Contrôle : {len(controle):,} ({dc:%Y-%m-%d} → {d1:%Y-%m-%d})"
    )

    ge, gc = cellules(etude, args.tz_shift), cellules(controle, args.tz_shift)
    le = juger(ge, "étude")
    lc = {(c["critere"], c["valeur"]): c for c in juger(gc, "contrôle")}
    if not le:
        raise SystemExit("Aucune cellule assez fournie.")

    seuil = NormalDist().inv_cdf(1.0 - ALPHA / (2.0 * len(le)))
    print(f"{len(le)} cellules comparables, seuil de t {seuil:.2f} (Bonferroni).\n")

    print(
        f"{'critère':<12}{'valeur':<9}{'n':>7}{'rendement':>11}{'t':>7}"
        f"{'frais':>8}  │{'rendement':>11}{'t':>7}"
    )
    print(f"{'':<12}{'':<9}{'':>7}{'— étude —':>18}{'':>8}  │{'— contrôle —':>18}")
    print("-" * 82)

    changent = 0
    for c in le[: args.top]:
        hors = lc.get((c["critere"], c["valeur"]))
        marque = "*" if abs(c["t"]) > seuil else " "
        if hors and c["moyenne"] * hors["moyenne"] < 0:
            marque = "!"
            changent += 1
        d = (
            f"{hors['moyenne']:>+10.1f}p{hors['t']:>7.2f}"
            if hors
            else f"{'—':>18}"
        )
        print(
            f"{marque}{c['critere']:<11}{c['valeur']:<9}{c['n']:>7}"
            f"{c['moyenne']:>+10.1f}p{c['t']:>7.2f}{c['frais']:>8.0f}  │{d}"
        )

    if changent:
        print(
            f"\n! = {changent} cellule(s) changent de signe hors échantillon.\n"
            "  C'est le verdict le plus net qui soit : ce n'était pas un effet,\n"
            "  c'était du bruit."
        )

    # ── Puissance ────────────────────────────────────────────────────────
    meilleure = max(le, key=lambda c: c["n"])
    detectable = seuil * meilleure["ecart"] / meilleure["n"] ** 0.5
    rentable = statistics.fmean(o["spread"] for o in etude)
    print("\n── Puissance de ce balayage ───────────────────────")
    print(f"Cellule la mieux fournie          : {meilleure['n']:,} observations")
    print(f"Plus petit effet détectable       : {detectable:.1f} points")
    print(f"Plus petit effet rentable         : {rentable:.1f} points (spread moyen)")
    if detectable <= rentable:
        print(
            "\nLa détection est plus fine que le seuil de rentabilité.\n"
            "Un avantage exploitable aurait été vu. Ce balayage conclut."
        )
    else:
        facteur = (detectable / rentable) ** 2
        print(
            "\nLa détection est plus grossière que le seuil de rentabilité.\n"
            f"Il faudrait environ {facteur:.1f} fois plus d'observations pour\n"
            "trancher. Ce balayage ne conclut rien."
        )

    # ── Verdict ──────────────────────────────────────────────────────────
    survivants = [c for c in le if abs(c["t"]) > seuil]
    print("\n── Verdict ────────────────────────────────────────")
    if not survivants:
        print("Aucune cellule ne franchit le seuil statistique sur l'étude.")
        return 0
    for c in survivants:
        hors = lc.get((c["critere"], c["valeur"]))
        couvre = abs(c["moyenne"]) > c["frais"]
        etat = "couvre ses frais" if couvre else f"NE COUVRE PAS ({c['frais']:.0f} p)"
        rejoue = (
            f"hors échantillon {hors['moyenne']:+.1f} p, t = {hors['t']:.2f}"
            if hors
            else "absente hors échantillon"
        )
        print(
            f"  {c['critere']} = {c['valeur']} : {c['moyenne']:+.1f} points sur "
            f"{c['n']:,} obs, t = {c['t']:.2f}\n    {etat} — {rejoue}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
