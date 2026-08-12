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
| imbalance « bougie sans mèche » | M1 | prémisse **inversée** : le bord est comblé moins souvent qu'un témoin apparié ; +0,004 R à spread nul sur 52 477 trades |

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

Le M1, lui, ne concluait rien : détection 0,167 contre 0,126 requis, il faudrait
environ deux fois plus d'observations.

**Le plafond de 100 000 bougies a été levé le 2026-08-10.** Il portait sur
`copy_rates`, pas sur `copy_ticks_range`. Sondage du même jour : `copy_rates`
refuse au-delà de 100 000 bougies (≈ 101 jours en M1), mais les ticks remontent
à **au moins 30 mois**. L'historique exploitable était donc environ neuf fois
plus profond que ce que le projet utilisait, et disponible tout de suite — pas
« à accumuler dans le temps ».

`agreger_ticks()` reconstruit les bougies depuis les ticks, sur le **bid**
comme MetaTrader. Validation contre les bougies du courtier : **27 544 bougies
communes, 100,00 % identiques au centime**.

**Le M1 conclut désormais, et il conclut non.** Reconstruit sur 24 mois :
696 371 bougies depuis 121,7 millions de ticks, soit **7 fois le plafond**.

| | à 100 000 bougies | à 696 371 bougies |
|---|---|---|
| effet détectable | 0,167 ATR | **0,013 ATR** |
| effet rentable (18 pts) | 0,126 ATR | 0,141 ATR |
| le balayage conclut ? | **non** | **oui**, détection 11× plus fine |

Trois cellules franchissent le seuil statistique, **aucune ne couvre ses
frais** : elles rendent 0,021 à 0,026 ATR quand il en faudrait 0,097 à 0,105.
Hors échantillon, aucune ne franchit le seuil des deux côtés et cinq changent
de signe.

Le détail qui compte est le même qu'à 30 secondes : `sens de la bougie =
baissière` **tient hors échantillon** (+0,022 ATR, t = 4,22 en étude ; +0,020,
t = 3,09 en contrôle). C'est un effet réel et reproductible. Il rend cinq fois
moins que ce que coûtent les frais.

Conclusion : le manque de données n'était pas la cause. À toutes les échelles
mesurées — 30 s, 60 s, M1, M5 — il existe de la structure réelle, et elle est
systématiquement **un ordre de grandeur sous le coût de passage**.

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

### L'imbalance de bougie sans mèche : la prémisse est inversée

Testée le 2026-08-11, reprise de `bot-scalping-gold/no_wick.py` qui la mesurait
sur M15 — hors contraintes — et la laissait sans verdict.

Prémisse : une bougie qui ferme sans mèche d'un côté laisse une « imbalance »
que le prix reviendrait combler, en retouchant le bord sans mèche (l'ouverture).

**Le test original était ininterprétable faute de témoin.** Il annonçait un taux
de remplissage sans jamais mesurer à quelle fréquence le prix revient sur
l'ouverture d'une bougie *quelconque*. Or le prix revient constamment sur ses
pas : 80 % de remplissage peut très bien être **moins** que le hasard.

Mesuré en M1 sur 696 371 bougies, avec témoin apparié — mêmes bougies, même
cible, sans la condition « sans mèche ». Corps ≥ 100 points, 52 478 signaux :

| fenêtre | sans mèche | témoin | écart |
|---|---|---|---|
| 3 bougies | 37,2 % | 43,9 % | **−6,7 %** |
| 6 bougies | 51,7 % | 56,8 % | −5,1 % |
| 20 bougies | 71,6 % | 75,1 % | −3,5 % |
| 50 bougies | 81,7 % | 83,9 % | −2,3 % |

**L'écart est négatif à toutes les fenêtres, et sans filtre de taille aussi**
(−9,9 % à 3 bougies sur les 170 062 signaux). Le bord d'une bougie sans mèche
est comblé **moins souvent** que l'ouverture d'une bougie ordinaire. C'est
cohérent : une bougie sans mèche est une impulsion, le prix s'en éloigne.

Attention à ne pas en déduire l'inverse comme stratégie. Avec un stop
symétrique, la course entre le bord et le stop donne **50,2 % de réussite** et
**+0,004 R à spread nul** sur 52 477 trades, quand l'écart-type de l'espérance y
vaut 0,004 R : exactement pile ou face, dans un sens comme dans l'autre. Le
moindre remplissage tient à la durée illimitée de la mesure de fréquence, pas à
un avantage dans la course. Avec frais : −0,067 R à 12 points.

`compensation.py` du même projet est bâtie sur cette imbalance (tendance +
comblement + continuation). Sa base ne portant aucune information, elle hérite
d'un fondement nul — à tester seulement si une raison nouvelle apparaît.

**Les autres pistes de ce projet sont hors contraintes** : `pbd.py`, `lvn_v2.py`
et `meanrev.py` déclarent toutes `SYMBOL = "USTECH"` — elles portent sur le
NASDAQ, pas sur l'or. C'est pourquoi elles n'ont jamais eu de verdict ici.

### `momentum récent = Q1` : un vrai petit effet, 3,7 fois trop petit

Suivi le 2026-08-11, parce qu'il était le seul à ressortir **plus fort hors
échantillon qu'en étude** — l'inverse du motif d'un artefact.

La correction de la dérive a d'abord tranché à moitié : le t d'étude s'effondre
de 2,30 à **0,61**, mais celui de contrôle tient, 3,73 → **3,54**. Normal : la
dérive valait +1,1 point sur l'étude contre +0,2 sur le contrôle, donc la
retirer ne pouvait pas affecter les deux périodes de la même façon.

Restait à savoir si le contrôle était un régime ou un accident.
`stabilite_cellule.py` découpe les 24 mois en huit tranches :

| | |
|---|---|
| Sous-périodes de signe positif | **7 / 8** |
| Effet par sous-période | −4,7 à +4,6 points |
| Aucune ne franchit son seuil | t max 2,10 pour 3,27 exigé |

Ce n'est donc **pas une bouffée** : l'effet est là presque partout, trop faible
pour être significatif sur 5 800 observations à la fois. Mesure poolée sur les
24 mois complets :

| | |
|---|---|
| Effet | +0,026 ATR = **3,3 points** |
| t | **2,65** — sous le seuil de 3,27 |
| Frais exigés (ATR propre à la cellule) | 0,097 ATR = **12,4 points** |

**Verdict : probablement un retour à la moyenne réel, et inexploitable.** Il ne
franchit pas le seuil de significativité une fois corrigé du nombre de cellules,
et surtout il rend **3,7 fois moins que son propre coût**. Même lecture qu'à 30
et 60 secondes : le retour à la moyenne existe à toutes les échelles, il pèse
2 à 5 points, les frais en coûtent 9 à 24.

Ne pas y revenir en espérant plus de données : l'obstacle n'est pas la
significativité, c'est l'ordre de grandeur.

### Les réfutations tiennent au coût corrigé

Rejeu du 2026-08-11 sur le moteur corrigé, M5 sauf mention. Les verdicts avaient
été rendus à 24 points de spread, alors que la période en valait ≈ 18 et que le
régime actuel est à 12 — il fallait vérifier qu'aucun ne basculait.

| stratégie | 24 pts | 18 pts | 12 pts |
|---|---|---|---|
| `smc` | −0,052 R | −0,043 R | **−0,015 R** |
| `fade` | −0,128 R | −0,120 R | −0,068 R |
| `asian-sweep` | −0,190 R | −0,186 R | −0,188 R |
| `vol-break` (M1) | −0,159 R | −0,179 R | −0,136 R |
| `orb` | 2 trades | 3 trades | 3 trades |

**Aucune ne devient positive.** Diviser le spread par deux améliore `smc` de
+0,037 R et `fade` de +0,060 R, sans suffire.

**La nuance qui compte, et elle porte sur `smc`.** À 12 points elle vaut
−0,015 R sur 1 138 trades, quand l'écart-type de l'espérance y est de **0,030 R**.
Elle n'est donc plus distinguable de zéro. Ce n'est pas un encouragement : une
espérance nulle ne paie rien, la lecture est faite sur l'échantillon complet et
non sur une validation, et `smc` reste négative **frais retirés** — ce qui ne
doit rien au spread.

`asian-sweep` ne bouge quasiment pas (−0,190 → −0,188) : ses stops sont assez
larges pour que le spread y pèse peu. Son problème n'a jamais été le coût.

### L'entrée à l'ordre limite : impossible ici, et déjà mesurée

Fermée le 2026-08-10. L'idée était d'échapper au spread en entrant sur un
niveau posé à l'avance plutôt qu'au marché. Elle bute sur une contrainte
d'exécution, pas sur une mesure.

**Sur MT5, un ordre limite traverse le spread comme un ordre au marché.** Un
*buy limit* s'exécute quand l'**ask** atteint le niveau, un *sell limit* quand
le **bid** l'atteint. `broker.py` le modélise correctement : le remplissage
d'un achat renvoie `bid_fill + spread`, inconditionnellement. Se poster au bid
et se faire toucher suppose d'être fournisseur de liquidité sur un carnet, avec
rétrocession *maker* — ça n'existe pas en CFD retail.

L'ordre limite n'est donc pas un outil pour **économiser** le spread, mais pour
**attendre un meilleur prix**, et il se paie en trades manqués.

**Et c'était déjà mesuré sans qu'on le sache.** `strategy.py` fixe
`entry_type = "limit"` par défaut et `SmcStrategy` ne le surcharge jamais :
**`smc` entre à l'ordre limite depuis toujours**. Seules les stratégies de
`scalping.py` forcent `entry_type = "market"`. Or `smc` est négative, et
négative **frais retirés**. L'entrée limite a donc été éprouvée sur ~450 trades
hors échantillon sans jamais rien sauver.

### Le remplissage au simple contact fabriquait un faux positif

Trouvé en examinant le moteur avant de tester quoi que ce soit — la bonne
méthode, et elle a payé.

Le courtier simulé remplissait un ordre limite **dès que la bougie touchait son
niveau**, en totalité et au meilleur prix de l'excursion. C'est faux : il faut
que le marché traite au-delà du niveau pour purger la file d'attente. Un plus
bas qui vient effleurer le niveau au centième près ne sert personne.

`limit_fill_margin_points` (défaut **1 point**, option `--limit-margin`) exige
désormais une traversée réelle. Sensibilité mesurée sur `smc`, M5, spread 12 :

| marge | trades | espérance | winrate |
|---|---|---|---|
| **0 pt** (ancien modèle) | 1 214 | **+0,007 R** | 33,6 % |
| **1 pt** (défaut) | 1 138 | **−0,015 R** | 32,9 % |
| 5 pts | 1 061 | −0,028 R | 32,4 % |
| 20 pts | 864 | −0,091 R | 30,3 % |
| 50 pts | 645 | −0,141 R | 28,7 % |

**Un seul point de traversée fait changer le signe.** Le biais valait +0,022 R
par trade et suffisait à rendre `smc` positive. Il flattait exactement les
entrées limite, donc précisément la piste qu'on s'apprêtait à explorer.

1 point est le minimum qui ait un sens — le prix doit avoir coté au-delà, pas
seulement touché — mais **ce n'est pas une valeur mesurée** : la vraie
probabilité d'être servi au plus bas d'une bougie est bien inférieure à 1.
Avant tout verdict sur une stratégie à entrée limite, faire varier
`--limit-margin` et vérifier que la conclusion tient. La colonne ci-dessus
montre qu'elle se dégrade continûment : aucune valeur ne la sauve.

### Payer moins de spread : la question est mal posée

Cherché le 2026-08-10. Trois constats, dans l'ordre où ils se sont imposés.

**Le modèle de coût est juste.** `broker.py` traite les bougies comme du bid,
achète à l'ask et sort au bid : le spread est facturé **une fois** par
aller-retour, pas deux. Rien à récupérer de ce côté.

**Baisser le spread ne suffira jamais.** Les avantages réels et reproductibles
mesurés valent 2 à 3 points :

| effet, validé hors échantillon | vaut | il faudrait un spread de |
|---|---|---|
| `sens de la bougie` baissière, M1 | 0,021 ATR = **2,7 points** | < 2,7 |
| retour à la moyenne, 30 s | **2,3 points** | < 2,3 |
| retour à la moyenne, 60 s | **2,1 points** | < 2,1 |

Le meilleur spread jamais relevé sur ce compte est de **10 points**. Il
faudrait donc diviser encore par quatre le meilleur cas, sur un instrument dont
le spread institutionnel tourne autour de 2 à 3 points sans marge pour le
glissement. Passer de 18 à 11 points fait gagner 1,6× quand il en faudrait 4 à
7. **Aucun courtier ne comble cet écart.**

**Tenir plus longtemps ne le comble pas non plus — et le mesurer naïvement
fabrique un faux positif.** Le spread est fixe par aller-retour, donc allonger
la détention devrait le diluer. L'effet grandit bien avec l'horizon :

| horizon | `sens baissière` | `corps Q5` | frais requis |
|---|---|---|---|
| 3 bougies | +0,021 A | +0,023 A | 0,105 A |
| 12 bougies | +0,058 A | — | 0,105 A |
| 60 bougies | — | **+0,464 A** | 0,108 A |

À 60 bougies une cellule couvrait enfin ses frais. **C'était un artefact, et sa
cause est instructive.** Le rendement inconditionnel de l'or est linéaire en
horizon, alors que le spread est fixe :

| horizon | dérive, étude | dérive, contrôle | spread |
|---|---|---|---|
| 3 bougies | +1,1 pt | +0,2 pt | 18 |
| 60 bougies | **+22,7 pt** | +4,7 pt | 18 |

Sur la période d'étude l'or est passé de 2 524 à 4 119, soit +63 %. À 60
bougies, la dérive seule dépassait le spread — **sans aucune condition**. Toutes
les cellules ressortaient positives parce qu'elles héritaient de la hausse, pas
parce qu'elles apprenaient quoi que ce soit. Hors échantillon la dérive retombe
à +4,7 points et tout s'effondre : `corps Q5` passe de +0,462 à +0,176, t = 0,87.

**Correction apportée à l'outil.** `edge-scan` juge désormais chaque cellule sur
son **excès** par rapport au rendement moyen inconditionnel, pour la
significativité comme pour la couverture des frais. Tester une moyenne contre
zéro revient à demander « l'or a-t-il bougé ? » — vrai partout dans un marché
qui monte. Après correction, la cellule qui passait à t = 3,39 ne passe plus
(la plus forte tombe à t = −2,93 pour 3,27 exigé). Deux tests verrouillent ça,
dont un contrôle négatif sur une dérive pure.

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
| `outils_mesure/stabilite_cellule.py` | une cellule d'`edge-scan` tient-elle sur toutes les sous-périodes, ou par bouffées | non |
| `outils_mesure/bougies_depuis_ticks.py` | reconstruit des bougies depuis les ticks, au-delà du plafond de 100 000 du courtier | non |
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
- **Ordre limite rempli au simple contact** : remplir dès que la bougie touche
  le niveau accorde un service certain, en totalité, au meilleur prix de
  l'excursion — alors qu'il faut traiter au-delà pour purger la file d'attente.
  Valait **+0,022 R par trade** et rendait `smc` positive. Une traversée d'un
  seul point suffit à inverser le signe. Régler `--limit-margin` et vérifier
  que tout verdict sur une entrée limite tient quand on le fait varier.
- **Cellule jugée contre zéro dans un marché en tendance** : tester une moyenne
  contre zéro demande « l'or a-t-il bougé ? », vrai pour toutes les cellules
  d'un marché qui monte. La dérive inconditionnelle est linéaire en horizon
  quand le spread est fixe, donc le biais **croît avec l'horizon** : à 60
  bougies, +22,7 points de dérive dépassaient à eux seuls les 18 points de
  spread. Juger sur l'**excès** par rapport au rendement inconditionnel.
- **Tranche de ticks qui coupe une bougie en deux** : si la borne d'un
  téléchargement ne tombe pas sur un début de bougie, chaque moitié part dans
  une tranche différente et l'ouverture reconstruite est celle du **milieu** de
  la bougie. Erreur mesurée jusqu'à **165 points**, et invisible : 99,99 % des
  bougies restaient justes, seules celles des frontières étaient fausses.
  Aligner les bornes sur des débuts de bougie.
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

## Travailler sans supervision

Le propriétaire n'est pas développeur et ne veut pas arbitrer chaque étape.
**Ne pas lui demander de choisir entre deux pistes techniques** : trancher,
expliquer le choix en deux lignes, et avancer. Il interviendra s'il n'est pas
d'accord.

### Ce qui se décide seul

Écrire et modifier du code, ajouter des tests, lancer backtests, balayages et
mesures, corriger un défaut découvert en chemin, committer et pousser sur la
branche de travail. Choisir quelle piste explorer ensuite.

### Ce qui ne se décide jamais seul

- **Passer un ordre, sur quelque compte que ce soit.** Le dépôt n'a aucune
  couche d'exécution réelle et ne doit pas en acquérir. `paper` reste une
  simulation ; l'activer en continu se demande.
- **Dépenser de l'argent** : frais d'évaluation d'une prop firm, abonnement à
  des données, compte réel.
- **Changer les réglages du compte** de trading ou du terminal.
- **Modifier ou supprimer les contraintes de ce fichier.** Elles viennent du
  propriétaire, pas d'une mesure.
- **Réinitialiser le compteur d'hypothèses.**

### Ordre de priorité quand une piste se ferme

1. **Un défaut de mesure suspecté prime sur tout le reste.** Un chiffre
   flatteur est un chiffre à vérifier : dans ce projet, chacun cachait un
   bug — lookahead, stop appliqué à sa propre bougie, décomposition lue sur
   l'apprentissage, paramètres écrasés en silence, dérive prise pour un signal.
2. **Une approximation encore non levée** vaut mieux qu'une idée neuve : elle
   se mesure, alors qu'une idée se teste et consomme une hypothèse.
3. **Un levier de l'ordre de grandeur du problème.** Les effets réels valent
   2 à 3 points, le spread en coûte 9 à 24 : une piste qui améliore de 0,5
   point ne sert à rien, quelle que soit son élégance.
4. **Une nouvelle source d'information**, plutôt qu'une forme de plus sur la
   même source. Sept familles de motifs de prix ont échoué ; la huitième
   échouera aussi.

### Rendre compte

Après chaque avancée, dire en quelques lignes : ce qui a été mesuré, le
chiffre obtenu, et ce qu'il ferme ou ouvre. Pas de tableau de bord, pas de
question ouverte en fin de message — sauf si une décision de la liste
« jamais seul » est réellement en jeu.

## Règles de méthode

- Toute conclusion se lit sur la **période de validation**, jamais sur
  l'apprentissage.
- Le compteur d'hypothèses (`runtime/hypotheses.json`) persiste entre sessions
  et relève le seuil de significativité. Ne pas le réinitialiser sans changer
  de jeu de données.
- Aucun verdict en dessous de 30 trades de chaque côté.
- Les identifiants MT5 passent uniquement par les variables d'environnement
  `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER`, jamais en ligne de commande.
