# ScalpXAU — scalping tick par tick sur l'or

## 1. L'arithmétique, avant tout le reste

Le spread est **fixe**. Le mouvement capturable croît environ comme la **racine
du temps de détention**. Ces deux faits décident de la viabilité d'un scalp
avant toute considération de stratégie.

Mesuré sur un compte Pepperstone, spread 24 points, amplitude médiane d'une
bougie M5 de 359 points :

| Fenêtre | Amplitude totale typique | Spread en % de l'amplitude |
|---|---|---|
| 5 s | ~46 pts | **52 %** |
| 10 s | ~66 pts | **37 %** |
| 30 s | ~114 pts | **21 %** |
| 60 s | ~161 pts | 15 % |
| 5 min | 359 pts | 6,7 % |

Et il s'agit de l'amplitude *totale* (haut moins bas), pas du mouvement
directionnel qu'un trade capture — lequel est nettement plus petit.

Traduit en taux de réussite nécessaire, avec un objectif à 1,5 fois le stop :

| Stop | Frais en R | Réussite nécessaire à l'équilibre |
|---|---|---|
| sans frais | 0 | 40,0 % |
| 400 pts | 0,06 | 42,4 % |
| 200 pts | 0,12 | 44,8 % |
| 120 pts | 0,20 | 48,0 % |
| 80 pts | 0,30 | 52,0 % |
| 40 pts | 0,60 | **64,0 %** |

C'est pourquoi l'EA **refuse de démarrer** quand le spread dépasse
`MaxCostRatio` du stop. Ce n'est pas un réglage à forcer.

**La contradiction à connaître** : pour que les frais restent sous 30 % du
risque il faut un stop d'au moins 80 points, mais un stop de 80 points est
rarement atteint en 30 secondes. La plupart des trades expireront donc sur
`MaxHoldSeconds` **à plat, après avoir payé le spread**. Si 60 % des trades
sortent ainsi, cela fait −0,12 R par trade avant même de parler de la qualité
du signal.

## 2. Logique de la stratégie

### Signal d'entrée

Sur une fenêtre glissante de `WindowMs` millisecondes (défaut 1500), l'EA
mesure deux quantités sur les prix médians :

- **déplacement net** = `prix_final − prix_initial` — où le prix est allé ;
- **chemin parcouru** = somme des `|Δ|` tick à tick — combien il a bougé pour
  y aller.

Leur rapport est l'**efficacité**. Proche de 1, le prix avance en ligne droite,
ce qui trahit un intervenant travaillant un ordre dans une direction. Proche de
0, il oscille sans aller nulle part : c'est le régime majoritaire de l'or, et
celui où le spread se paie pour rien.

Entrée si, simultanément :

1. `|déplacement net| ≥ MinImpulseSpreads × spread_courant` (défaut 2,0×) ;
2. `efficacité ≥ MinEfficiency` (défaut 0,60) ;
3. au moins `MinTicks` ticks dans la fenêtre (défaut 8) ;
4. `spread ≤ MaxSpreadPoints` (défaut 35) ;
5. `spread / stop ≤ MaxCostRatio` (défaut 0,30) ;
6. hors fenêtre d'annonce, dans la session, aucune position ouverte, aucune
   limite de risque atteinte, et `CooldownAfterMs` écoulé depuis la dernière
   entrée.

Direction = signe du déplacement net. C'est une stratégie de **continuation**,
pas de retour à la moyenne.

**Le seuil est exprimé en multiples du spread courant, jamais en points
absolus.** C'est le seul point qui rende la règle stable : un seuil de « 50
points » serait exigeant à 9h quand le spread est à 18, et laxiste à 23h quand
il est à 90.

### Sortie

Par ordre de priorité :

1. **Stop loss** — `StopLossPoints`, posé sur le serveur dès l'ouverture.
2. **Take profit** — `TakeProfitRatio × StopLossPoints`.
3. **Temps écoulé** — `MaxHoldSeconds`. Ce sera la sortie majoritaire.
4. **Inversion de l'impulsion** — si `ExitOnReversal` et que le même calcul de
   signal pointe dans la direction opposée.

### Conditions de non-trade

Spread trop large, ratio de coût dépassé, hors session, fenêtre d'annonce,
pause après pertes consécutives, perte journalière atteinte, drawdown maximal
atteint, marge insuffisante, volume calculé sous le lot minimal, terminal
déconnecté, stop plus serré que le minimum du courtier.

### Pourquoi ça pourrait fonctionner sur l'or — et pourquoi ça peut échouer

L'argument favorable : l'or est un marché à forte participation algorithmique
où les ordres importants sont fractionnés. Un ordre travaillé produit une
séquence de ticks directionnels dont la continuation à quelques secondes est le
seul phénomène de court terme documenté ailleurs que dans le marketing.

L'argument défavorable, et il est fort : cette continuation dure typiquement
moins d'une seconde et se mesure en quelques points. Le spread retail de 24
points est du même ordre de grandeur que le phénomène entier. Les acteurs qui
exploitent réellement ce signal paient un spread institutionnel de 2 à 5 points
et sont colocalisés.

## 3. Installation

1. Copier `ScalpXAU.mq5` dans `MQL5/Experts` du dossier de données du terminal
   (*Fichier → Ouvrir le dossier de données*).
2. Ouvrir MetaEditor (F4), sélectionner le fichier, compiler (F7).
   Vérifier **0 erreur, 0 avertissement**.
3. Dans MetaTrader, activer *Outils → Options → Expert Advisors → Autoriser le
   trading algorithmique*, et le bouton **Algo Trading** de la barre d'outils.
4. Ouvrir un graphique XAUUSD, glisser l'EA dessus, cocher *Autoriser le trading
   algorithmique* dans l'onglet Commun.
5. Lire l'onglet **Experts** : l'EA imprime la valeur du point, les lots
   min/max/pas, le stops level, le ratio de coût et le taux de réussite exigé.
   **Si ces chiffres ne te semblent pas cohérents, n'active rien.**

### Réglages recommandés au départ

| Paramètre | Valeur | Raison |
|---|---|---|
| `StopLossPoints` | 120 | Frais à 20 % du risque avec un spread de 24 |
| `TakeProfitRatio` | 1,5 | Équilibre à 48 % de réussite |
| `MaxHoldSeconds` | 30 | Au-delà, ce n'est plus du tick scalping |
| `MaxSpreadPoints` | 35 | Au-delà, l'or est en régime nocturne ou de news |
| `MaxCostRatio` | 0,30 | Refus de démarrer au-delà |
| `RiskPercent` | 0,5 | |
| `MinImpulseSpreads` | 2,0 | L'impulsion doit valoir deux fois ce qu'elle coûte |
| `MinEfficiency` | 0,60 | Écarte le bruit oscillant |

Sur un compte à 1 000 €, `RiskPercent` 0,5 % donne 5 € de risque. Avec un stop
de 120 points et une valeur du point de 0,8652 €/lot, le volume théorique est
de 0,048 lot — au-dessus du lot minimal, donc jouable. **Un stop supérieur à
577 points serait refusé** (volume sous 0,01 lot).

## 4. Protocole de test — obligatoire

### Backtest

Strategy Tester, mode **« Chaque tick basé sur les ticks réels »**
exclusivement. Les autres modes interpolent les ticks à partir des bougies M1 :
pour une stratégie qui décide à l'intérieur d'une seconde, ils fabriquent des
résultats sans rapport avec la réalité.

- Période : **3 mois minimum**, spread réel (pas fixe).
- Métriques : profit factor **> 1,3**, drawdown maximal, nombre de trades,
  répartition par session.
- Vérifier la ligne « Trades expirés sur le temps » dans le journal : si elle
  dépasse 70 %, la stratégie ne capture rien et paie le spread en boucle.

**Une réserve que je dois énoncer** : même en ticks réels, le tester n'a aucun
modèle de latence. Il exécute à l'instant du signal. En réel, 20 à 150 ms
s'écoulent entre la décision et le remplissage — sur un trade de 5 secondes,
c'est 1 à 3 % de la durée de détention et souvent plus que le mouvement visé.
**Un backtest favorable ne prouve donc rien ici.** Il ne sert qu'à éliminer les
configurations manifestement perdantes.

### Forward test

**4 semaines minimum sur compte démo**, sur un compte démo du **courtier
cible**. Les spreads démo et réel diffèrent — souvent nettement, et toujours
dans le sens défavorable au réel. Comparer les statistiques imprimées par l'EA
à celles du backtest : un écart important sur le taux de réussite est le signal
que la latence ou le spread réel détruit l'avantage supposé.

Aucun euro réel avant d'avoir ces quatre semaines.

## 5. Limites honnêtes

Situations où cet EA perdra de l'argent :

- **Spread élargi** — nuit, ouverture du dimanche, jours fériés. Le filtre
  `MaxSpreadPoints` protège, mais le spread s'élargit *pendant* le trade aussi,
  et la sortie le paie.
- **Annonces économiques** — la liste d'heures est statique et ne couvre pas
  les déclarations imprévues. Un mouvement de 500 points en deux secondes
  traverse le stop avec un slippage arbitraire.
- **Faible volatilité** — le filtre d'impulsion coupe les entrées, l'EA ne
  trade pas. C'est le comportement voulu, mais cela veut dire des journées
  entières sans trade.
- **Latence** — c'est le facteur principal. Sur une connexion domestique
  (50–200 ms), une partie du mouvement visé est déjà consommée au remplissage.
  Un VPS proche du serveur du courtier réduit le problème sans l'annuler.
- **Politique du courtier** — beaucoup de courtiers retail restreignent ou
  annulent les profits issus de trades de moins de 60 secondes. **Lis les
  conditions générales avant de lancer quoi que ce soit en réel.**
- **Arrondi du volume** — sur un petit compte, le pas de 0,01 lot fait que le
  risque réel s'écarte du risque visé de plusieurs dizaines de pour cent.

Différences attendues entre backtest et réel : taux de réussite plus faible,
slippage à l'entrée et à la sortie, spread moyen plus élevé, et des sorties sur
le temps plus fréquentes.

## 6. Checklist avant de lancer en démo

- [ ] Compilé avec **0 erreur et 0 avertissement**.
- [ ] L'onglet Experts affiche une valeur du point cohérente avec la fiche du
      symbole dans MT5.
- [ ] Le ratio de coût affiché est sous 30 %, et le taux de réussite exigé te
      paraît atteignable — sinon, ne lance pas.
- [ ] `StopLossPoints` est supérieur au stops level affiché par l'EA.
- [ ] Le volume calculé pour ton capital est au-dessus du lot minimal.
- [ ] Backtest ticks réels sur 3 mois effectué, profit factor et drawdown notés.
- [ ] Proportion de sorties sur le temps relevée dans le journal.
- [ ] Compte **démo** sélectionné, pas réel — vérifie le titre de la fenêtre.
- [ ] `MaxDailyLossPercent` et `MaxDrawdownPercent` réglés à des valeurs que tu
      acceptes de perdre.
- [ ] Magic number distinct de celui de tout autre EA sur ce compte.
- [ ] Conditions générales du courtier lues concernant le scalping.
- [ ] Date de fin des 4 semaines de test notée quelque part.

## 7. Ce que ce fichier n'a pas

Il n'a **jamais été compilé** : je n'ai pas MetaEditor. La syntaxe a été
vérifiée à la main et l'équilibre des blocs contrôlé automatiquement, mais si
la compilation renvoie une erreur, envoie-la-moi telle quelle et je la corrige.
