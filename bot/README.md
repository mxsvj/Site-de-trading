# smcbot — bot SMC pour MetaTrader 5

Bot de trading basé sur les *Smart Money Concepts* : biais donné par la structure
de marché, entrée sur retour dans un order block, stop derrière la zone.

Il fait deux choses, et seulement deux :

- **backtest** — rejoue la stratégie sur un historique, bougie par bougie ;
- **paper trading** — la fait tourner en continu sur un compte simulé.

**Aucun ordre réel n'est envoyé, à aucun moment.** Il n'y a pas de couche
d'exécution live dans ce dépôt : rien à désactiver, rien qui puisse partir tout
seul par accident.

---

## Installation

Le cœur du bot (moteur SMC, backtest, paper trading) **n'a aucune dépendance** :
Python 3.10 ou plus suffit, sur Windows, macOS ou Linux.

```bash
cd bot
pip install -r requirements.txt
```

Le paquet `MetaTrader5` ne s'installe que sous Windows — c'est une limite du
paquet lui-même, pas du bot. Sans lui, tout fonctionne sauf la connexion directe
à MT5 : il suffit de travailler à partir d'un CSV.

## Démarrage immédiat

Sans aucune donnée ni MT5, sur une série synthétique :

```bash
python -m smcbot backtest --demo
```

```
EURUSD M15 — 3000 bougies du 2024-01-01 au 2024-02-01
Signaux générés : 43
┌─ Résultats ────────────────────────────────
│ Trades              : 43  (21 long / 22 short)
│ Winrate             : 34.9 %  (15G / 28P / 0N)
│ Profit factor       : 1.10
│ Espérance           : +0.068 R par trade
...
```

> Ces chiffres ne veulent **rien** dire : la série de démo est une marche
> aléatoire. Elle sert à vérifier que la mécanique tourne, pas à évaluer la
> stratégie. Selon la graine, le résultat est positif ou négatif — c'est
> exactement ce qu'on attend d'un marché sans structure exploitable.

## Avec tes vraies données

### Option A — export manuel depuis MT5 (fonctionne partout)

Exporte l'historique en CSV depuis MT5 (ou depuis n'importe quelle autre source
de données OHLC). Ensuite :

```bash
python -m smcbot backtest --csv EURUSD_M15.csv --symbol EURUSD --timeframe M15
```

Le chargeur accepte les en-têtes usuels (`time,open,high,low,close,volume`) comme
le format tabulé de MT5 (`<DATE>  <TIME>  <OPEN> ...`).

### Option B — téléchargement direct (Windows, MT5 ouvert)

```bash
python -m smcbot download --symbol EURUSD --timeframe M15 --bars 20000 --out data/eurusd_m15.csv
python -m smcbot backtest --csv data/eurusd_m15.csv --symbol EURUSD
```

### Renseigner ton instrument

Les valeurs par défaut correspondent à EURUSD 5 digits sur un compte en USD.
Pour tout le reste, génère un fichier de configuration et corrige-le :

```bash
python -m smcbot init-config --out config.json
python -m smcbot backtest --csv data/xauusd_m15.csv --config config.json
```

Les champs à vérifier en priorité dans `symbol` : `point`, `digits`,
`value_per_point_per_lot`, `min_lot`, `lot_step`, `spread_points` et
`commission_per_lot`. Ils se lisent dans MT5 en faisant un clic droit sur le
symbole dans la fenêtre *Observation du marché* → **Spécification**.
Un `value_per_point_per_lot` faux fausse **tout** le dimensionnement.

## Paper trading

Hors ligne, en rejouant un historique à pleine vitesse (utile pour voir le
comportement décision par décision) :

```bash
python -m smcbot paper --csv data/eurusd_m15.csv --interval 0 --max-bars 2000
```

En direct sur MT5 (Windows, terminal ouvert et connecté) :

```bash
python -m smcbot paper --mt5 --symbol EURUSD --timeframe M15 --interval 30
```

Le bot interroge MT5 toutes les 30 secondes, ne traite que les bougies
**clôturées**, journalise chaque décision dans `runtime/paper.log` et sauvegarde
son état dans `runtime/paper_state.json` après chaque bougie.

```
2026-08-06 17:37:30 | INFO | ENTREE achat 0.57 lot @ 1.09974 | SL 1.09887 | TP 1.10148 | OB bullish #313 + FVG + cassure @ 1.10133
2026-08-06 17:37:30 | INFO | SORTIE achat 0.57 lot @ 1.10148 | TP | +99.18 (+2.00R) | solde 10049.50
```

**Arrêt propre** : crée le fichier `runtime/STOP` (ou `Ctrl+C`). La boucle
termine la bougie en cours puis s'arrête en affichant le rapport.

## Mode scalping (XAUUSD M1, biais M15)

```bash
python -m smcbot backtest --preset xauusd-scalp --demo --demo-bars 20000
```

Le preset `xauusd-scalp` change trois choses par rapport au mode swing.

### 1. Le biais vient d'une unité de temps supérieure

La structure est lue sur **M15**, les entrées cherchées sur **M1**. Les bougies
M15 sont reconstruites à partir des M1 et ne sont transmises au moteur qu'une
fois **terminées** — le biais accuse donc le retard qu'il aurait en direct.
Sans ça, la structure M1 prise seule n'est guère que du bruit.

```bash
python -m smcbot backtest --csv xauusd_m1.csv --timeframe M1 --htf M15 --symbol-preset xauusd
```

Option supplémentaire `--htf-zone` : n'entrer que si l'order block M1 recoupe un
order block ou un FVG M15.

### 2. Un filtre horaire

Par défaut, uniquement Londres (07:00-11:00 UTC) et New York (13:00-17:00 UTC),
du lundi au vendredi. Hors de ces plages, le spread s'élargit et le mouvement
disparaît — deux raisons de ne pas payer le péage.

> Les heures sont en **UTC**, pas dans le fuseau de ton serveur MT5 (souvent
> UTC+2 ou UTC+3). Vérifie l'horodatage de tes bougies avant de régler les plages.

### 3. Des filtres de coût — le point vraiment important

Ton stop définit ton R ; le spread et la commission, eux, sont fixes. Plus le
stop est serré, plus ils pèsent :

| Stop (or) | Frais à 25 pts de spread | Part du risque |
|---|---|---|
| 500 pts (5,00 $) | 25 pts | 5 % |
| 200 pts (2,00 $) | 25 pts | 13 % |
| 100 pts (1,00 $) | 25 pts | **25 %** |
| 50 pts (0,50 $) | 25 pts | **50 %** |

À 50 points de stop, il te faut un avantage brut supérieur à 0,5 R avant de
gagner le moindre dollar. D'où trois garde-fous : `min_stop_points` (plancher de
stop), `max_cost_ratio` (part maximale du risque absorbée par les frais) et
`max_spread_points` (refus quand le spread s'élargit).

Ces deux premiers doivent rester **cohérents entre eux** : avec 25 points de
spread et un plafond de frais à 30 %, le stop minimal réellement praticable est
25 / 0,30 ≈ 84 points. Le preset le fixe à 100 points pour cette raison — sinon
le plancher de stop ne servirait à rien, le plafond de frais rejetant tout avant
lui. C'est vérifié par `test_preset_scalping_est_coherent`.

Le backtest te dit ce que les filtres ont écarté :

```
Setups écartés par les filtres : 38
     38 × stop trop serré
```

Effet mesuré sur 20 000 bougies M1 de démonstration :

| Variante | Trades | Écartés |
|---|---|---|
| Aucun filtre | 230 | 0 |
| Filtre horaire seul retiré | 84 | 80 |
| Plancher de stop retiré | 49 | 25 |
| Tous les filtres | 38 | 38 |

Passer de 230 à 38 trades **est** l'objectif : chaque trade évité est un péage
non payé.

### Ce que le scalping coûte en fiabilité de backtest

Mes règles conservatrices (stop prioritaire quand SL et TP tombent dans la même
bougie, pas de TP sur la bougie d'entrée) sont des détails à M15. À M1, elles
décident d'une part importante des trades, parce que le mouvement intrabar est
grand par rapport à l'objectif. **Un backtest de scalping sur bougies M1 reste
indicatif** ; seule une simulation sur données tick trancherait vraiment.

Ajoute à cela ce que le backtest ne modélise pas et qui frappe plus fort en
scalping qu'en swing : slippage, élargissement du spread sur news, requotes,
délai d'exécution. Sur M1, ces coûts arrivent dix fois plus souvent que sur M15.

### En paper trading

```bash
python -m smcbot paper --preset xauusd-scalp --mt5 --interval 5
```

Descends l'intervalle à 5 secondes : sur M1, une bougie clôture chaque minute.

## Recherche de paramètres

```bash
python -m smcbot optimize --csv data/eurusd_m15.csv --grid-tp 1.5,2,3 --grid-swing 2,3,4
```

Classement par espérance en R. À manier avec précaution : sur une grille large,
le meilleur résultat est surtout le mieux surajusté. Teste toujours le gagnant
sur une période qui n'a pas servi à le choisir.

---

## La stratégie

Le schéma recherché est celui qu'enseigne le site :

1. **Liquidité prise** *(optionnel)* — une mèche dépasse un ancien extrême puis
   clôture en deçà : les stops ont été balayés.
2. **Cassure de structure** — le prix clôture au-delà du dernier swing de
   référence. Première cassure dans un sens = **CHoCH** (retournement), les
   suivantes = **BOS** (continuation). C'est ce qui fixe le biais.
3. **Order block** — la dernière bougie de couleur opposée avant l'impulsion qui
   a cassé la structure.
4. **Déséquilibre (FVG)** — un écart à trois bougies dans la jambe impulsive,
   signe que le mouvement est parti trop vite pour être équilibré. Exigé par
   défaut (`require_fvg`).
5. **Entrée** — au retour du prix dans l'order block ; stop derrière la zone,
   take profit à un multiple du risque.

Un order block est **abandonné** si le prix clôture au-delà de sa zone
(invalidation), s'il a déjà servi, ou après `ob_max_age` bougies.

### Détail qui compte : aucune information future

Un swing n'est confirmé qu'après `swing_lookback` bougies de chaque côté. Le
moteur est incrémental — on lui pousse les bougies une par une et il ne voit
jamais la suite. Ce n'est pas qu'une intention : le test
`test_absence_de_lookahead` rejoue un préfixe de la série et vérifie que les
trades produits sont **identiques** à ceux du backtest complet. Si le code
trichait avec le futur, tronquer la série changerait le passé.

## Ce que le backtest suppose

Ces hypothèses sont volontairement défavorables. Un backtest optimiste ne sert à rien.

| Situation | Hypothèse retenue |
|---|---|
| Stop et take profit dans la même bougie | **le stop**, toujours |
| Position ouverte en cours de bougie | peut être stoppée sur cette même bougie, mais **jamais** gagner son TP dessus |
| Spread | bougies en bid ; achat exécuté à l'ask, stop d'une vente déclenché à l'ask. Le coût est porté par le risque réel du trade |
| Passage à *breakeven* | appliqué en **fin** de bougie, il ne vaut qu'à partir de la suivante |
| Volume calculé sous le lot minimal | trade **refusé** plutôt que sur-risqué |
| Arrondi du volume | toujours à l'inférieur |

Ce qui n'est **pas** modélisé, et qui existe en vrai : slippage, élargissement du
spread sur news, rejets et requotes, swap/rollover overnight, gaps du week-end,
décalage d'exécution. Compte tenu de tout ça, un backtest est un plancher de
plausibilité, pas une prévision.

## Configuration

| Section | Champ | Défaut | Rôle |
|---|---|---|---|
| `smc` | `swing_lookback` | 3 | bougies de chaque côté pour valider un swing |
| | `ob_lookback` | 12 | profondeur de recherche de l'order block |
| | `ob_use_body` | false | zone sur le corps plutôt que sur la mèche |
| | `ob_max_age` | 60 | péremption d'un OB non mitigé |
| | `require_fvg` | true | exiger un déséquilibre dans l'impulsion |
| | `require_sweep` | false | exiger une prise de liquidité avant la cassure |
| | `entry_at_equilibrium` | false | entrer à 50 % de l'OB au lieu du bord |
| `risk` | `initial_balance` | 10 000 | capital de départ |
| | `risk_pct` | 0.5 | % du capital risqué par trade |
| | `tp_r` | 2.0 | take profit en multiple du risque |
| | `sl_buffer_points` | 20 | marge du stop sous/au-dessus de la zone |
| | `max_concurrent` | 1 | positions simultanées |
| | `max_daily_loss_pct` | 3.0 | seuil d'arrêt pour la journée |
| | `breakeven_at_r` | 0 | passage à BE à N R (0 = désactivé) |
| `filters` | `sessions` | — | plages UTC autorisées, ex. `["07:00-11:00"]` |
| | `weekdays` | lun→ven | jours autorisés (0 = lundi) |
| | `max_spread_points` | 0 | spread maximal toléré (0 = off) |
| | `max_cost_ratio` | 0 | part max. du risque en frais (0.30 = 30 %) |
| | `min_stop_points` | 0 | distance minimale du stop |
| | `max_trades_per_day` | 0 | plafond quotidien (0 = illimité) |
| (racine) | `timeframe` | M15 | unité de temps des entrées |
| | `htf` | — | unité de temps du biais (vide = mono-timeframe) |

Tout est aussi surchargeable en ligne de commande : `--risk`, `--tp-r`,
`--swing`, `--spread`, `--sl-buffer`, `--breakeven`, `--no-fvg`, `--sweep`,
`--equilibrium`, `--htf`, `--htf-zone`, `--sessions`, `--no-sessions`,
`--max-spread`, `--max-cost`, `--min-stop`, `--max-trades-day`.
`python -m smcbot backtest --help` liste le reste.

Deux configurations prêtes à l'emploi via `--preset` : `eurusd-m15` (swing) et
`xauusd-scalp` (scalping M1/M15). `--symbol-preset eurusd|xauusd` ne reprend que
les caractéristiques du contrat.

## Structure du code

```
bot/
├── smcbot/
│   ├── config.py      configuration (instrument, SMC, risque, filtres, presets)
│   ├── data.py        bougies, CSV, MT5, rééchantillonnage, flux
│   ├── smc.py         moteur SMC : swings, BOS/CHoCH, order blocks, FVG, liquidité
│   ├── strategy.py    signaux, biais multi-timeframe
│   ├── filters.py     sessions, spread, coût relatif, plafond quotidien
│   ├── risk.py        dimensionnement des positions, P&L, R
│   ├── broker.py      courtier simulé : exécution, spread, stops, kill switch
│   ├── backtest.py    boucle de backtest et exports CSV
│   ├── metrics.py     winrate, profit factor, drawdown, espérance, Sharpe
│   ├── paper.py       boucle de paper trading, journal, persistance de l'état
│   └── cli.py         interface en ligne de commande
└── tests/             81 tests
```

Le backtest et le paper trading utilisent **le même** moteur SMC et **le même**
courtier simulé : ce qui a été validé en backtest se comporte à l'identique en
simulation live. C'est vérifié par `test_paper_trading_identique_au_backtest`.

## Tests

```bash
cd bot && python -m pytest
```

```
81 passed
```

Ils couvrent la détection SMC (swings, CHoCH/BOS, order blocks, FVG, sweeps),
le dimensionnement des positions, la mécanique d'exécution (spread, priorité du
stop, breakeven, kill switch), l'absence de lookahead et la cohérence
backtest ↔ paper trading.

## Utiliser le bot depuis du code

```python
from smcbot import BotConfig, load_csv, run_backtest

cfg = BotConfig()
cfg.risk.risk_pct = 0.5
cfg.risk.tp_r = 3.0
cfg.smc.require_sweep = True

result = run_backtest(load_csv("data/eurusd_m15.csv"), cfg)
print(result.report.to_text())
result.save_trades("trades.csv")
```

---

## Avertissement

Ceci est un outil d'étude. Le trading avec effet de levier fait perdre de
l'argent à la grande majorité de ceux qui s'y essaient, et un backtest favorable
ne prédit rien — surtout après avoir cherché les bons paramètres sur les mêmes
données. Si tu passes un jour en réel, fais-le sur un compte démo pendant
plusieurs mois d'abord, puis avec des montants dont la perte totale te serait
indifférente.
