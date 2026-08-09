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

**Régularité à retenir.** Trois échelles de temps séparées par un facteur 300,
avec et sans frais, donnent toutes un taux de réussite **un point sous le seuil
d'équilibre à coût nul**. Les motifs de prix, à toutes ces échelles, ne portent
aucune information directionnelle sur l'or.

Conséquence pratique : chercher un cinquième motif de prix a peu de chances
d'aboutir. Les pistes qui restent portent sur **le coût d'exécution** et sur
**les conditions dans lesquelles on trade**, pas sur la forme du signal.

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
