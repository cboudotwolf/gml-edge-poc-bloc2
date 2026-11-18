# Vérifications réalisées

## Tests automatisés

Commande :

```bash
python3 -m unittest discover -s tests -v
```

- Python 3.12.14 : 30 tests, tous réussis.
- Python 3.14.0 : 30 tests, tous réussis.

Les tests incluent les comportements métier, les unités, les bordures, la
protection des fichiers d’entrée et les erreurs contrôlées. Les sorties sont
calculées à partir des coordonnées et valeurs mesurées, indépendamment des
annotations `expected`.

## Démonstration sur les données originales

- États GPS : OUT, OUT, OUT, IN, IN.
- Rejets par Bounding Box : 2 positions.
- Alerte ZFE : point 4, à 08:00:30.
- Désaccord signalé avec l’annotation du point 3 : 1.
- Échantillons CSV valides : 10.
- Événement détecté : 1, à 1678880005 secondes Unix.
- Pic Y brut : −3,45 m/s² ; pic filtré : −2,904 m/s².
- Score pédagogique : 90/100 pour les échantillons analysés.
- Cadence moyenne observée du CSV : 1 Hz.

Avec un seuil littéral de 2,5 G, le même CSV ne contient aucun dépassement.
Ce résultat est attendu et testé.

## Flux synthétique à 100 Hz

Commande exécutée sous Python 3.12.14 :

```bash
python3 tools/benchmark.py --samples 100000
```

Résultat observé sur l’ordinateur d’exécution :

- 100 000 échantillons à une cadence simulée de 100 Hz ;
- 1 freinage détecté ;
- traitement en 1,424 seconde ;
- maximum mémoire du processus : 21,3 Mio.

Ces mesures ne sont pas une validation sur tablette Zebra. Le test matériel,
l’acquisition Android, la calibration des axes et les contraintes temps réel
restent à réaliser avant tout usage en circulation.
