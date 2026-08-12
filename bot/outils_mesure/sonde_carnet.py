"""Le courtier diffuse-t-il une profondeur de marché exploitable sur l'or ?

Tout ce que le projet a mesuré vient du prix seul. Le carnet d'ordres serait le
premier champ d'information réellement nouveau — encore faut-il qu'il existe.

Piège rencontré le 2026-08-11 : une première sonde a renvoyé zéro niveau sur
l'or et dix sur EURUSD, ce qui semblait fermer la piste. En réalité l'or était
dans sa coupure quotidienne (21:00–22:00 UTC) et EURUSD non. Sonder un marché
fermé ne prouve rien. Ce script attend donc que des ticks arrivent avant de
conclure quoi que ce soit.

`ticks_bookdepth` est déclaratif : le courtier annonce 5 niveaux sur l'or, la
même valeur que sur EURUSD. Reste à savoir s'il les diffuse vraiment.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time


def marche_ouvert(mt5, symbole: str, secondes: int = 90) -> bool:
    """Un tick récent est la seule preuve fiable que le marché cote."""
    t = mt5.symbol_info_tick(symbole)
    if t is None or t.time == 0:
        return False
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromtimestamp(
        t.time, tz=dt.timezone.utc
    )
    # Le serveur est en UTC+3 : un tick « frais » paraît daté du futur de 3 h.
    return abs(age.total_seconds()) % 3600 < secondes or age.total_seconds() < 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument(
        "--attente-max", type=int, default=75, help="minutes d'attente maximale"
    )
    ap.add_argument(
        "--echantillon", type=int, default=120, help="secondes d'échantillonnage"
    )
    args = ap.parse_args()

    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 init KO : {mt5.last_error()}")
    try:
        mt5.symbol_select(args.symbol, True)
        spec = mt5.symbol_info(args.symbol)
        print(
            f"{args.symbol} : ticks_bookdepth déclaré = {spec.ticks_bookdepth}",
            flush=True,
        )

        # ── Attendre que le marché cote ──────────────────────────────────
        precedent = mt5.symbol_info_tick(args.symbol).time
        debut = time.time()
        while time.time() - debut < args.attente_max * 60:
            time.sleep(20)
            courant = mt5.symbol_info_tick(args.symbol).time
            if courant != precedent:
                print(
                    f"Marché ouvert : le tick a bougé après "
                    f"{(time.time() - debut) / 60:.1f} min d'attente.",
                    flush=True,
                )
                break
            precedent = courant
        else:
            print("Aucun tick nouveau : marché resté fermé. Sonde non concluante.")
            return 2

        # ── Échantillonner le carnet ─────────────────────────────────────
        if not mt5.market_book_add(args.symbol):
            print(f"Souscription refusée : {mt5.last_error()}")
            return 2

        vus, vides, profondeurs = 0, 0, []
        exemple = None
        fin = time.time() + args.echantillon
        while time.time() < fin:
            book = mt5.market_book_get(args.symbol)
            vus += 1
            if book:
                profondeurs.append(len(book))
                if exemple is None:
                    exemple = book
            else:
                vides += 1
            time.sleep(0.5)
        mt5.market_book_release(args.symbol)

        print(f"\n{vus} relevés en {args.echantillon} s : "
              f"{len(profondeurs)} avec carnet, {vides} vides.")
        if not profondeurs:
            print(
                "\nAucun niveau reçu alors que le marché cotait.\n"
                "Le courtier annonce une profondeur qu'il ne diffuse pas sur cet\n"
                "instrument : la piste du carnet est fermée pour l'or."
            )
            return 1

        print(f"Profondeur : min {min(profondeurs)}, max {max(profondeurs)}")
        print("\nExemple de carnet :")
        for e in exemple:
            genre = {1: "vente", 2: "achat"}.get(e.type, str(e.type))
            print(f"  {genre:<7} prix {e.price:<12} volume {e.volume}")
        print(
            "\nCarnet réellement diffusé : la piste est ouverte. Prochaine\n"
            "étape, mesurer si le déséquilibre du carnet précède le prix."
        )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
