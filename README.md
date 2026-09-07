# Site-de-trading

Deux pages statiques, sans build ni dépendances : il suffit d'ouvrir le fichier.

| Fichier | Contenu |
|---|---|
| `index.html` | **Flux** — l'académie de trading (leçons, boîte à outils, examen). |
| `paris-sportifs.html` | **Flux Odds** — l'analyseur de paris sportifs. |

## Flux Odds — analyseur de paris sportifs

**Football, basket et tennis.** Compare les cotes de tous les bookmakers d'une
compétition, analyse la forme récente des équipes, et croise les deux pour ne
signaler que ce qui a une valeur mathématique.

### Ce qu'il calcule

**Pour les trois sports** — comparaison multi-bookmakers : meilleure cote par
issue, marge de chaque book, retrait de cette marge (Shin, puissance ou
proportionnelle) pour retrouver la vraie estimation de chacun, puis consensus
pondéré vers les books les plus fiables. Détection des **surebets** avec la
répartition des mises. Quand les bookmakers ne cotent pas tous la même ligne
(224,5 chez l'un, 225,5 chez l'autre), l'outil retient celle que le plus de
books proposent et écarte les autres, en le disant : comparer des cotes portant
sur des lignes différentes n'a aucun sens.

| | Modèle | Marchés | Joueurs |
|---|---|---|---|
| **Football** | Poisson corrigé Dixon-Coles sur les buts | 1X2, plus/moins, les deux marquent | oui — probabilité de marquer par joueur |
| **Basket** | Loi normale sur l'écart et le total, écart-type mesuré sur les matchs joués | vainqueur, total, handicap | non ([pourquoi](#limites)) |
| **Tennis** | aucun ([pourquoi](#limites)) | vainqueur | non |

Les forces offensive et défensive de chaque équipe sont estimées sur tous ses
matchs, les récents pesant davantage, avec un ajustement domicile/extérieur
limité à ce que l'échantillon peut soutenir. Les mises sont calculées par le
critère de Kelly fractionné et plafonné.

Le poids par défaut du modèle est de **20 %** contre 80 % au marché. Ce n'est pas
arbitraire : sur une saison, les probabilités du modèle portent environ 6 points
de pourcentage d'erreur d'estimation, contre 2 à 3 pour le marché. Pondérer
chaque estimation par l'inverse de son erreur donne 20 %. C'est aussi pourquoi le
seuil d'écart minimum est à 4 % — en dessous, on lirait le bruit du modèle pour
de la valeur.

### Rester gratuit

L'outil télécharge une compétition entière en environ 5 requêtes, puis analyse
tous les matchs à venir en local et simultanément — au lieu d'une requête par
match. Les réponses sont mises en cache dans le navigateur (calendrier 8 h,
marqueurs 24 h, cotes 15 min, liste des compétitions 30 jours), un compteur
affiche en permanence le quota restant, et l'outil refuse de dépasser le palier
gratuit.

- [API-Sports](https://dashboard.api-sports.io/register) — 100 requêtes par
  jour, sans carte bancaire. Calendrier, résultats, cotes et marqueurs, pour le
  football et le basket. Prendre la clé sur `dashboard.api-sports.io` : une clé
  issue de `api-football.com` ne couvre que le football.
- [The Odds API](https://the-odds-api.com/) — 500 crédits par mois.
  Indispensable au tennis ; ailleurs, élargit la liste des bookmakers. La liste
  des tournois est gratuite et illimitée.

Sans aucune clé, le **mode démo** fonctionne sans limite pour les trois sports,
avec le même moteur d'analyse. Ses équipes, joueurs, matchs et cotes sont
**entièrement inventés** — noms fictifs, championnats fictifs, et un bandeau
d'avertissement sur chaque écran. Aucune de ces rencontres n'existe : il doit
être impossible de miser de l'argent réel sur ce que la démo affiche.

Les clés restent dans le navigateur (`localStorage`) et ne sont envoyées qu'aux
API concernées.

### Diagnostic

Un onglet **Diagnostic** appelle les mêmes adresses que le scanner et vérifie,
ligne par ligne, que chaque chose se trouve là où le code va la chercher :
équipes, scores, bookmakers, chaque marché — et il liste les paris qu'il n'a
*pas* su lire. Utile la première fois pour savoir si tout fonctionne, et plus
tard le jour où un fournisseur change sa réponse sans prévenir, au lieu d'un
scanner qui échoue sans raison visible.

Il coûte jusqu'à 3 requêtes, zéro si les données sont en cache, et produit un
rapport copiable dont la clé est retirée avant affichage.

### Limites

**Pas de statistiques joueur au basket** : l'API ne les fournit pas sur son
palier gratuit, et aller les chercher équipe par équipe brûlerait le quota en
quelques matchs.

**Pas de modèle en tennis** : aucune API de statistiques tennis n'a de palier
gratuit — ni classement, ni forme, ni surface, ni confrontations directes. Un
modèle sans ces données produirait des probabilités qui ont l'air sérieuses sans
l'être. L'outil s'en tient donc à la comparaison des bookmakers, qui reste utile :
les écarts entre books y sont plus larges que dans les grands championnats.

**Dans tous les cas**, l'outil ignore les blessures, les compositions, le
contexte d'un match et la qualité réelle des occasions. Un écart signalé est une
hypothèse chiffrée, pas une prédiction. Interdit aux mineurs ; en France, ne
jouer que chez un opérateur agréé par l'ANJ.
