# Contraintes du projet

Décidées par le propriétaire du dépôt. Elles ne sont pas à rediscuter : toute
proposition doit tenir dedans.

| Contrainte | Valeur |
|---|---|
| Instrument | **XAUUSD uniquement** |
| Style | **Scalping** |
| Unité de temps | **de la seconde à M1** |
| Fréquence visée | **beaucoup de trades** |
| Capital de référence | 1 000 € |
| Risque par trade | 0,5 % |

Ne pas proposer de monter en unité de temps, ni de changer d'instrument, ni de
réduire la fréquence. Si une mesure montre qu'une piste ne marche pas, chercher
une autre piste **à l'intérieur de ces contraintes**.

## Ce que ces contraintes impliquent, chiffré

Courtier de référence : Pepperstone, spread mesuré **24 points** sur l'or,
valeur du point **0,8652 €** pour 1 lot, lot minimum **0,01**.

**Plafond de stop.** Pour risquer 5 € (0,5 % de 1 000 €) au lot minimum, le stop
ne peut pas dépasser `5 / (0,01 × 0,8652)` = **578 points**. Au-delà, le volume
calculé tombe sous 0,01 lot et le trade est refusé — ce n'est pas un réglage,
c'est le pas de lot du courtier.

**Plancher de stop.** Le spread est prélevé sur le risque. À 24 points, un stop
de 120 points laisse 20 % du risque aux frais, un stop de 80 points en laisse
30 %, un stop de 40 points en laisse 60 %.

La fenêtre praticable est donc **≈ 100 à 578 points de stop**, ce qui
correspond à des horizons de l'ordre de la minute à la dizaine de minutes.

## Ce qui a déjà été réfuté — ne pas y revenir sans raison nouvelle

Mesuré sur 17 mois de données réelles (100 000 bougies M5, 2025-03 → 2026-08)
et 17,6 millions de ticks.

| Stratégie | Horizon | Résultat |
|---|---|---|
| `smc` (order block, FVG, BOS) | M1, M5 | espérance nulle à négative, ~450 trades hors échantillon |
| `smc`, **frais retirés** | M5 | **encore négative** — le signal lui-même ne vaut rien |
| `asian-sweep` | M1, M5 | −0,28 R |
| `fade` (retour à la moyenne) | M5 | négative sur 564 trades d'apprentissage |
| `orb` | M5 | injouable : stop > 578 points, trades refusés |
| Sortie sur le temps (3 à 24 bougies) | M5 | aucun effet, écart de 0,03 R = bruit |
| `ScalpXAU` (momentum tick, EA MQL5) | 1,5 s | 39 % de réussite pour 48 % requis, arrêt sur drawdown |
| `vol-break` (cassure à volatilité gated) | M1 | −0,071 R sur 706 trades ; brut, frais retirés : **−0,018 R** |
| `lead-lag` (retard de l'or sur EUR/USD) | M1 | −0,178 R hors échantillon, pire qu'en apprentissage |
| `edge-scan` (46 conditions × 3 horizons) | M1, M5 | aucune condition à la fois significative et rentable |

**Régularité à retenir.** Trois échelles de temps séparées par un facteur 300,
avec et sans frais, donnent toutes un taux de réussite **un point sous le seuil
d'équilibre à coût nul**. Les motifs de prix, à toutes ces échelles, ne portent
aucune information directionnelle sur l'or.

Conséquence pratique : chercher un motif de prix de plus a peu de chances
d'aboutir. Les pistes qui restent portent sur **le coût d'exécution** et sur
**les conditions dans lesquelles on trade**, pas sur la forme du signal.

### Le balayage systématique, et pourquoi son résultat compte

`edge-scan` ne teste aucune stratégie : il mesure le rendement futur de l'or
sous 46 conditions observables (heure, jour, volatilité, momentum, forme de
bougie), sans stop, sans objectif, sans spread. Sur le M5, 33 328 observations
disjointes couvrant 17 mois :

- finesse de détection **0,033 ATR**, seuil de rentabilité **0,061 ATR** ;
- la détection est donc **plus fine que ce qu'il faudrait pour gagner** ;
- une seule cellule franchit le seuil statistique — `volatilité Q2`, +0,061 ATR,
  t = 3,50 — et elle **ne couvre pas ses propres frais** (0,084 ATR exigés) ;
- elle change de signe hors échantillon (+0,058 → −0,000).

Ce résultat n'est pas « on n'a rien trouvé ». C'est **« un avantage
exploitable aurait été vu »**. La différence est décisive : elle interdit de
conclure qu'il manque des données.

Le M1, lui, ne conclut rien : détection 0,167 contre 0,126 requis, il faudrait
deux fois plus d'observations. Le courtier plafonnant à 100 000 bougies
(≈ 101 jours en M1), la seule voie est d'accumuler l'historique dans le temps.

### Approximation encore non levée

Tous les backtests supposent un **spread constant de 24 points**. Sur l'or il
double ou triple hors des séances de Londres et New York. Les résultats
obtenus avec `--no-sessions` sont donc **optimistes**, et les stratégies déjà
réfutées le sont encore plus qu'affiché. `spread-profile` mesure le spread
réel heure par heure depuis les ticks — à lancer sur la machine Windows, et à
reporter dans les seuils de coût.

## Outils disponibles

| Commande | Rôle | Consomme des hypothèses |
|---|---|---|
| `lab` | comparer des stratégies, correction de Bonferroni incluse | oui |
| `backtest` | rejouer une stratégie, avec tableau des motifs de refus | non |
| `optimize` | grille de réglages, décomposition du coût via `--grid-spread` | non |
| `edge-scan` | chercher une condition prédictive, avec contrôle de puissance | oui, en interne |
| `check` | qualité des données, fenêtre praticable de stops | non |
| `scan` | classer des instruments par coût de scalping | non |
| `spread-profile` | spread réel heure par heure, depuis les ticks | non |
| `download` | exporter un historique MT5 en CSV | non |

Les cinq dernières lignes marquées MT5 (`scan`, `spread-profile`, `download`,
`doctor`, `paper`) exigent MetaTrader ouvert sous Windows.

## Pièges déjà rencontrés — le code les prévient, ne pas les réintroduire

Chacun a produit un résultat faux et convaincant avant d'être trouvé.

- **Remplissage au marché à l'ouverture** de la bougie : lookahead valant
  +0,9 R par trade.
- **Stop appliqué à la bougie d'entrée** d'un ordre au marché : la bougie est
  close, ses extrêmes sont antérieurs à l'entrée. Coûtait 72 % de clôtures
  immédiates à `vol-break`.
- **Décomposition du coût lue sur l'apprentissage** : annonçait un avantage
  réel là où la validation était négative.
- **Verrou de perte journalière actif pendant une décomposition** : sans frais
  la stratégie se verrouille moins et trade 46 % de plus — deux populations
  différentes.
- **Capital qui décroît pendant une décomposition** : les trades à stop large
  passent sous le lot minimal et disparaissent. Neutraliser avec un capital
  élevé.
- **Paramètres de stratégie écrasés par la grille de `lab`** : tout ce qui
  venait de `--strategy-param` disparaissait en silence.
- **Seuil de rentabilité calculé sur l'ATR médian global** : flatte précisément
  les cellules à faible volatilité, où le spread pèse le plus.
- **Fenêtres de rendement qui se recouvrent** : gonflent la statistique t
  d'environ racine(horizon).

## Durée du travail

Le travail continue tant que le propriétaire n'est pas satisfait. Un résultat
négatif ferme une piste, il ne ferme pas le projet : chercher la suivante à
l'intérieur des contraintes ci-dessus. Ne pas proposer d'arrêter.

## Règles de méthode

- Toute conclusion se lit sur la **période de validation**, jamais sur
  l'apprentissage.
- Le compteur d'hypothèses (`runtime/hypotheses.json`) persiste entre sessions
  et relève le seuil de significativité. Ne pas le réinitialiser sans changer
  de jeu de données.
- Aucun verdict en dessous de 30 trades de chaque côté.
- Les identifiants MT5 passent uniquement par les variables d'environnement
  `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER`, jamais en ligne de commande.
