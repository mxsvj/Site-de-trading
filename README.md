# Site-de-trading

Deux pages statiques, sans build ni dépendances : il suffit d'ouvrir le fichier.

| Fichier | Contenu |
|---|---|
| `index.html` | **Flux** — l'académie de trading (leçons, boîte à outils, examen). |
| `paris-sportifs.html` | **Flux Odds** — l'analyseur de paris sportifs. |

## Flux Odds — analyseur de paris sportifs

Compare les cotes de tous les bookmakers d'une compétition, analyse la forme
récente des équipes et la cadence des joueurs, et croise les deux pour ne
signaler que ce qui a une valeur mathématique.

**Ce qu'il calcule**

- **Comparaison multi-bookmakers** — meilleure cote par issue, marge de chaque
  book, retrait de cette marge (Shin, puissance ou proportionnelle) pour
  retrouver la vraie estimation de chacun, puis consensus pondéré vers les
  books les plus fiables.
- **Surebets** — quand les meilleures cotes de plusieurs books couvrent toutes
  les issues pour moins de 100 %, avec la répartition des mises.
- **Modèle de buts** — forces offensive et défensive par équipe, séparées
  domicile / extérieur, à décroissance exponentielle sur les matchs récents et
  ramenées vers la moyenne du championnat sur les petits échantillons. Poisson
  corrigé Dixon-Coles pour le 1X2, les plus/moins de buts et « les deux
  marquent ».
- **Buteurs** — cadence par 90 minutes ajustée par ce que l'équipe est censée
  marquer dans ce match précis, et cote juste correspondante.
- **Value bets** — probabilité retenue (mélange modèle / marché) × meilleure
  cote, avec mise calculée par le critère de Kelly fractionné.

**Rester gratuit**

L'outil télécharge une compétition entière en environ 5 requêtes, puis analyse
tous les matchs à venir en local et simultanément — au lieu d'une requête par
match. Les réponses sont mises en cache dans le navigateur (calendrier 8 h,
buteurs 24 h, cotes 15 min), un compteur affiche en permanence le quota
restant, et l'outil refuse de dépasser le palier gratuit.

- [API-Football](https://dashboard.api-football.com/register) — 100 requêtes
  par jour, sans carte bancaire. Fournit calendrier, résultats, cotes et
  buteurs. Suffit à lui seul.
- [The Odds API](https://the-odds-api.com/) — facultatif, 500 crédits par mois,
  pour élargir la liste des bookmakers.

Sans aucune clé, le **mode démo** fonctionne sans limite : une saison simulée
et 14 bookmakers, avec le même moteur d'analyse.

Les clés restent dans le navigateur (`localStorage`) et ne sont envoyées qu'aux
API concernées.

**Ce que l'outil ne sait pas** : les blessures, les compositions, le contexte
d'un match, la qualité réelle des occasions. Un écart signalé est une hypothèse
chiffrée, pas une prédiction. Interdit aux mineurs ; en France, ne jouer que
chez un opérateur agréé par l'ANJ.
