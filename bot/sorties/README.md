# Sorties de commandes

Dossier tampon pour partager un résultat sans copier-coller.

Sur la machine Windows :

```powershell
python -m smcbot edge-scan --csv data/xauusd_m1.csv --timeframe M1 > sorties/edge-m1.txt 2>&1
git add sorties ; git commit -m "sortie edge-scan M1" ; git push
```

Le `2>&1` compte : sans lui, les erreurs partent ailleurs et le fichier
paraîtrait vide alors que la commande a échoué.

Contrairement à `bot/data/`, ce dossier n'est pas ignoré par git : c'est son
seul intérêt. Les fichiers sont petits, on peut les écraser sans scrupule.
