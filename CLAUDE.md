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

Courtier de référence : Pepperstone, compte **Retail** (`Retail\Commodities\
Gold\XAUUSD`), valeur du point **0,8652 €** pour 1 lot, lot minimum **0,01**.
**Aucune commission** : vérifié sur les deals 1 lot de l'historique du compte,
`commission = 0,0`. Le spread est donc le coût total.

**Le spread n'est pas une constante — mesuré, plus supposé.** 2026-08-10,
`spread-profile` sur 1,5 million de ticks (10 jours, serveur UTC+3 ramené en
UTC) :

| | points |
|---|---|
| médiane sur la journée | **11** |
| heures pleines, 06h–19h UTC | 11 |
| heures creuses, 20h–05h UTC | 16 à 17 |
| rapport creux / plein | **1,5** |

Et par fenêtres de 7 jours pleins réparties sur les 17 mois (donc à mélange
d'heures identique, comparables entre elles) :

| fenêtre | 2025-03 | 2025-06 | 2025-09 | 2025-12 | 2026-03 | 2026-06 | 2026-08 |
|---|---|---|---|---|---|---|---|
| médian | 18 | 15 | 21 | 23 | **24** | 12 | **12** |

Deux corrections en découlent, dans des directions opposées :

- Les **24 points** supposés partout ne sont pas la norme, c'est le **pire
  moment** de la période (2026-03). La moyenne sur les 17 mois de backtest est
  **≈ 18 points** : les résultats déjà obtenus ont été chargés d'environ **un
  tiers de frais en trop**.
- Le régime **actuel est de 12 points**, moitié moins que l'hypothèse. Ce qui
  est trop cher aujourd'hui ne l'est pas au même niveau qu'affiché.

**Plafond de stop.** Pour risquer 5 € (0,5 % de 1 000 €) au lot minimum, le stop
ne peut pas dépasser `5 / (0,01 × 0,8652)` = **578 points**. Au-delà, le volume
calculé tombe sous 0,01 lot et le trade est refusé — ce n'est pas un réglage,
c'est le pas de lot du courtier. Ce plafond ne dépend pas du spread.

**Plancher de stop.** Le spread est prélevé sur le risque, donc le plancher suit
le régime de spread — il n'y a pas un plancher, il y en a un par époque :

| part du risque mangée par les frais | à 24 pts | à 18 pts | à 12 pts |
|---|---|---|---|
| 20 % | 120 | 90 | **60** |
| 30 % | 80 | 60 | **40** |

La fenêtre praticable est donc **≈ 60 à 578 points de stop au régime actuel**,
contre ≈ 100 à 578 à 24 points. **Des horizons plus courts sont redevenus
jouables** — précisément la direction que demandent les contraintes (seconde à
M1). C'est la piste ouverte par cette mesure.

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
| `asymetrie_tick` (51 conditions) | **30 s, 60 s** | aucune condition significative ; le balayage **conclut** (détection 3 à 6 fois plus fine que le seuil de rentabilité) |

**Régularité à retenir.** Quatre échelles de temps, de 1,5 seconde à M5, avec et
sans frais, donnent toutes le même résultat : aucune information directionnelle
exploitable. Les motifs de prix, à toutes ces échelles, ne portent rien.

### La fenêtre 30–60 s, ouverte puis refermée le 2026-08-10

Elle méritait d'être ouverte : à 24 points de spread, le plus court horizon
finançable était d'environ 120 s ; à 12 points, il tombe à **30 s**. Cette
région était refusée par le coût, plus par les données. `ScalpXAU` échouait à
1,5 s, mais à cet horizon le spread vaut **171 %** du mouvement médian — perdu
d'avance quel que soit le signal, donc il ne concluait rien sur 30 s.

Mesuré sur 60 jours de ticks, fenêtres **disjointes**, 51 conditions, chaque
cellule jugée sur **son propre spread** lu dans le tick à l'instant de la
décision :

| horizon | observations | effet détectable | effet rentable | conclut ? |
|---|---|---|---|---|
| 30 s | 113 037 | 2,2 points | 13,0 points | **oui**, détection 6× plus fine |
| 60 s | 56 682 | 4,3 points | 13,0 points | **oui**, détection 3× plus fine |

Aucune cellule ne franchit le seuil de Bonferroni sur l'étude, et 5 changent de
signe hors échantillon. Comme sur le M5, ce n'est pas « on n'a rien trouvé »,
c'est **« un avantage exploitable aurait été vu »**.

**Le détail qui compte, et qui ferme la piste pour de bon.** Il y a bien de la
structure à cette échelle, et elle tient hors échantillon :

| cellule | étude | contrôle | frais |
|---|---|---|---|
| `spread Q1` (30 s) | −2,3 p | −2,3 p | 9 p |
| `momentum Q5` (30 s) | −2,1 p | −3,8 p | 13 p |
| `momentum baisse` (60 s) | +1,7 p | +2,1 p | 13 p |

Un léger retour à la moyenne, de signe stable dans les deux périodes. Mais il
pèse **2 à 4 points quand le spread en coûte 9 à 13** : l'effet réel est un
ordre de grandeur sous son propre coût. Baisser le spread de 24 à 12 points n'y
change rien — il faudrait le diviser encore par trois ou quatre.

Conséquence : inutile de revenir chercher un signal sous la minute. Ce n'est pas
une question de finesse de détection ni de quantité de données, c'est que ce qui
existe est trop petit d'un facteur 4 pour payer le passage du spread.

Conséquence pratique : chercher un motif de prix de plus a peu de chances
d'aboutir. Les pistes qui restent portent sur **le coût d'exécution** et sur
**les conditions dans lesquelles on trade**, pas sur la forme du signal.

### Le balayage systématique, et pourquoi son résultat compte

`edge-scan` ne teste aucune stratégie : il mesure le rendement futur de l'or
sous 46 conditions observables (heure, jour, volatilité, momentum, forme de
bougie), sans stop, sans objectif, sans spread. Sur le M5, 33 328 observations
disjointes couvrant 17 mois :

- finesse de détection **0,033 ATR** — elle ne dépend pas du spread ;
- seuil de rentabilité **proportionnel au spread**, donc pas unique ;
- une seule cellule franchit le seuil statistique — `volatilité Q2`, +0,061 ATR,
  t = 3,50 sur 6 659 observations ;
- elle **change de signe hors échantillon** (+0,058 → −0,000).

Le seuil de rentabilité étant proportionnel au spread, la mesure du 2026-08-10
oblige à relire ce résultat à trois niveaux de coût (vérifié en rejouant
`edge-scan --spread`) :

| spread | seuil de rentabilité | vs détection 0,033 | le balayage conclut-il ? |
|---|---|---|---|
| 24 pts (supposé) | 0,061 ATR | détection plus fine | **oui** |
| 18 pts (moyenne réelle de la période) | 0,046 ATR | détection plus fine | **oui** |
| 12 pts (régime actuel) | 0,030 ATR | détection plus **grossière** | **non** |

Ce qu'il faut en retenir, dans cet ordre :

1. Sur la période des backtests, le coût correct est 18 points, et à 18 points
   la conclusion **tient** : « un avantage exploitable aurait été vu ». Le
   résultat n'est pas annulé par la mesure de spread.
2. La cellule `volatilité Q2` reste morte quel que soit le coût — à 12 points
   elle couvre ses frais, mais elle change de signe hors échantillon, et c'est
   la validation qui tranche, pas la rentabilité théorique.
3. En revanche, **au régime actuel de 12 points le balayage ne conclut plus
   rien** : un avantage tout juste rentable (0,030 à 0,033 ATR) passerait
   maintenant sous la finesse de détection. À ce coût-là, « rien trouvé »
   redevient « pas assez d'observations ». C'est la seule chose que la baisse
   du spread rouvre côté signal — et elle demande plus de données, pas une
   idée de plus.

Le M1, lui, ne conclut rien : détection 0,167 contre 0,126 requis, il faudrait
deux fois plus d'observations. Le courtier plafonnant à 100 000 bougies
(≈ 101 jours en M1), la seule voie est d'accumuler l'historique dans le temps.

### L'arrondi du lot : 19 % du risque perdu, invisible en R

Mesuré le 2026-08-10. Le volume vaut `risque / (stop × valeur du point)`, puis
`risk.py` le **tronque** au pas du courtier (`math.floor`, jamais d'arrondi au
plus proche). À 1 000 € de capital le volume tourne entre 0,01 et 0,10 lot : la
troncature y pèse énormément.

Le point aveugle est ailleurs. **`r_multiple()` ne dépend que de distances de
prix** — le volume n'y entre pas. Tous les verdicts du projet sont en R, donc
aucun n'a jamais vu cet effet.

| | |
|---|---|
| Efficacité moyenne du risque (40 à 578 pts) | **81 %** |
| Mesurée sur un vrai backtest M1 | **79 %**, pire trade 51 % |
| Pire cas théorique | **50 %**, à un stop de 289 points |

**Les stops efficaces sont des lames de rasoir.** Le volume tombe pile sur un
multiple du pas quand `stop = 578 / k` : 577,90 · 288,97 · 192,63 · 144,48 ·
115,58 · 96,32 · 82,56 · 72,24 · 64,21 · 57,79. Un stop de **288,97** points
risque 5,00 € ; un stop de **289,00** points en risque 2,50 €. Trois centièmes
de point coûtent la moitié du risque.

**Piste explorée et refermée : ajuster le stop sur ces valeurs.** Comme ce sont
des points isolés et non des plages, tolérer 2 % de déplacement ne récupère que
1,2 point sur les 19 perdus ; il faudrait tolérer 10 % pour en gagner 6, et à ce
niveau on ne corrige plus un arrondi, on change le trade.

La cause n'est pas le placement du stop : à 1 000 € il n'existe que **dix
volumes distincts** sur toute la fenêtre praticable. Les seuls vrais remèdes
sont plus de capital ou un courtier au pas plus fin. À défaut, le backtest
affiche désormais `Risque réel / visé` et avertit sous 95 %.

Et le biais n'est pas uniforme — il frappe d'autant plus que le stop est large :

| tranche de stop | efficacité |
|---|---|
| 40–100 | 94 % |
| 100–200 | 88 % |
| 200–300 | 81 % |
| 300–450 | **65 %** |

Conséquence de méthode : deux stratégies de même espérance en R n'ont pas la
même espérance en euros si leurs stops diffèrent. **Comparer des stratégies en R
sans regarder la distribution des stops est faux.**

### Approximation levée le 2026-08-10

Cette section disait : « les backtests supposent 24 points constants ; sur l'or
le spread double ou triple hors Londres et New York, donc les résultats
`--no-sessions` sont **optimistes** ». La mesure contredit les deux moitiés de
cette phrase.

| ce qui était supposé | ce qui est mesuré |
|---|---|
| creux 2 à 3 fois plus cher | **1,5 fois** (11 → 17 points) |
| 24 points constants | **12 à 24 selon l'époque**, ≈ 18 en moyenne |
| résultats trop optimistes | trop **pessimistes** d'environ un tiers de frais |

Le biais allait donc dans l'autre sens : les stratégies déjà réfutées l'ont été
avec trop de frais, pas trop peu. Cela ne les réhabilite pas — `smc` et
`vol-break` restent négatives **frais retirés**, ce qui ne doit rien au
spread — mais tout jugement au bord du seuil est à refaire à 18 points sur la
période, et à 12 points pour ce qui se joue aujourd'hui.

**Ce qui reste non levé.** La mesure vient d'un compte **démo** ; les spreads
d'un compte réel peuvent être moins bons. À confronter le jour où un compte
réel existe. Le régime de 12 points date de 2026-06 : il est récent, et rien
ne garantit qu'il tienne — `spread-profile` est à relancer avant toute
décision qui en dépend.

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
| `outils_mesure/arrondi_lot.py` | ce que la troncature du volume retire au risque, et où sont les stops efficaces | non |
| `outils_mesure/excursion_tick.py` | de combien l'or bouge en 1 à 120 s, et ce que le spread y coûte | non |
| `outils_mesure/asymetrie_tick.py` | asymétrie directionnelle sous la minute, fenêtres disjointes et contrôle de puissance | oui, en interne |
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
- **Conclure en R sans regarder le risque réellement pris** : `r_multiple()` ne
  dépend que de distances de prix, donc l'espérance en R est aveugle à la
  troncature du volume, qui retire 19 % du risque en moyenne et jusqu'à 50 %.
  Le rapport affiche maintenant `Risque réel / visé` : le lire.
- **Test neutralisé par son propre montage** : `test_scan_exige_metatrader`
  posait `sys.modules["MetaTrader5"] = None` puis **supprimait** l'entrée, ce
  qui rendait l'import de nouveau possible. Le test ne passait que sur une
  machine sans MT5 — c'est-à-dire jamais sur la machine cible.
- **Spread relevé une fois et pris pour une constante** : les 24 points venaient
  d'un relevé fait au pire moment de la période. Le vrai spread va de 12 à 24
  selon l'époque, et le seuil de rentabilité d'`edge-scan` lui est
  proportionnel — de quoi faire conclure un balayage qui ne conclut rien.
  Relever le spread de la **période testée**, pas celui du jour du test.
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
