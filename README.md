# GML Edge — POC autonome ZFE et Safety

**Dépôt :** https://github.com/cboudotwolf/gml-edge-poc-bloc2

## 1. Lancer la démonstration en local

Prérequis : **Python 3.10 ou plus récent**, avec le module standard `sqlite3`.
Aucun paquet à installer avec pip.

```bash
git clone https://github.com/cboudotwolf/gml-edge-poc-bloc2.git
cd gml-edge-poc-bloc2
python3 main.py demo
```

Depuis une archive : décompresser, ouvrir un terminal dans le dossier contenant
`main.py`, puis exécuter la dernière commande. Sous Windows, remplacer `python3`
par `py -3`.

Résultat attendu sur les fichiers originaux :

```text
[GPS] point=3 heure=08:00:20 position=OUT
[ANNOTATION] point=3 : annexe=IN (Bordure) ; géométrie=OUT. Les coordonnées sont conservées.
[ALERT ZFE] entrée point=4 heure=08:00:30 position=IN
[FREINAGE VIOLENT] t=1678880005.0 brut=-3.450 m/s² filtré=-2.904 m/s²
```

Les fichiers générés sont :

- `runtime/daily_score.json` : résultats par jour UTC, événements et score pédagogique ;
- `runtime/events.sqlite3` : agrégats et événements stockés localement.

Pour lire le rapport :

```bash
python3 -m json.tool runtime/daily_score.json
```

Sur l’annexe, le rapport contient **10 échantillons valides**, **1 freinage** et un
**score de démonstration de 90/100**. Un exemple généré se trouve dans
`examples/daily_score.json`.

## 2. Tester chaque moteur séparément

### Geo : Bounding Box puis Ray Casting

```bash
python3 main.py geo --polygon data/lyon_polygon.json --gps data/truck_gps.json
```

Le rectangle englobant est calculé une fois à partir du polygone. Un point hors de
ce rectangle est rejeté immédiatement. Sinon, un rayon horizontal est lancé vers
la droite. Le nombre d’intersections avec les arêtes détermine la position :
impair = intérieur, pair = extérieur.

Les points sur une arête ou un sommet sont classés `BOUNDARY` et considérés inclus.
La tolérance numérique de `1e-10` degré ne représente **pas** une précision GPS.
Une alerte est émise à l’entrée, pas à chaque point situé dans la zone.

Le champ `expected` est utilisé uniquement pour signaler une incohérence de test.
Il ne décide jamais si le point est à l’intérieur du polygone.

Pour démontrer une véritable entrée par la bordure :

```bash
python3 main.py geo --gps data/truck_gps_boundary.json
```

Ce second jeu explicite remplace uniquement la longitude du point 3 par `4.8300`.
Le point `(lat=45.7850, lon=4.8300)` est le milieu de l’arête entre les deux premiers
sommets. Le fichier original reste inchangé.

**Coût du calcul :** préparation O(V), rejet Bounding Box O(1), Ray Casting O(V)
par position retenue, pour V sommets. Parcourir P positions et V arêtes correspond
à O(P × V), pas automatiquement à O(P²). Les JSON sont volontairement limités à
1 Mio par fichier pour ce POC.

### Safety : CSV bruité vers événements de freinage

```bash
python3 main.py safety --csv data/accelerometer_data.csv
```

Le CSV est lu ligne par ligne. Les quatre valeurs doivent être finies et les
timestamps strictement croissants. L’axe longitudinal du jeu est `acc_y` ;
la gravité présente sur `acc_z` n’est pas utilisée comme un freinage.

Le traitement suit cet ordre :

1. Petite bande morte : les valeurs de Y entre −0,15 et +0,15 m/s² sont ramenées à zéro.
2. Filtre exponentiel causal : `Y_filtré = 0.8 × Y_nettoyé + 0.2 × Y_filtré_précédent`.
3. Déclenchement si `Y_filtré < -2.5 m/s²`, strictement.
4. Regroupement des points successifs d’un même freinage. L’événement se termine
   lorsque la valeur remonte à au moins `-1.5 m/s²` (hystérésis).
5. Écriture de l’événement et de son horodatage dans SQLite, puis export JSON.

Le filtre est volontairement peu lissant : le pic brut de −3,45 m/s² reste à
−2,904 m/s² après filtrage. Une médiane ou un lissage trop fort risquerait
d’effacer ce pic isolé dans le petit jeu fourni. Ces paramètres sont des choix de
POC à recalibrer sur les capteurs réels, pas une méthode certifiée par l’assureur.

Les paramètres peuvent être modifiés :

```bash
python3 main.py safety --threshold-ms2 2.5 --alpha 0.8 --noise-floor 0.15
```

## 3. Trois incohérences de l’énoncé prises en compte

### Seuil : 2,5 G ou 2,5 m/s²

Le corps du sujet mentionne « 2.5G », mais l’annexe 3 indique explicitement un CSV
en m/s² et un seuil `acc_y < -2.5`. Le défaut du programme suit donc l’annexe 3.

Pour tester littéralement le seuil du corps du sujet :

```bash
python3 main.py safety --threshold-g 2.5 --output runtime/strict_g.json --db runtime/strict_g.sqlite3
```

Le programme convertit le seuil : `2.5 × 9.80665 = 24.516625 m/s²`.
Il détecte alors **zéro freinage** dans ce CSV : −3,45 m/s² ne franchit pas un seuil
de −24,516625 m/s². La distinction est explicite et testée.

### Point GPS 3 annoté « IN »

Le point original `(45.7850, 4.8050)` est à l’extérieur du polygone fourni, même
s’il porte l’annotation `IN (Bordure)`. Le moteur conserve les coordonnées et
signale ce désaccord. L’entrée géométrique réelle sur le fichier original est le
point **4**, à **08:00:30**.

### Cadence du CSV

Les dix timestamps du fichier sont espacés d’une seconde : le jeu est à **1 Hz**.
Il ne prouve pas à lui seul le fonctionnement d’un accéléromètre réel à 100 Hz.
Le rapport expose la cadence moyenne observée et la cible de 100 Hz séparément.

## 4. Format de daily_score.json

Le rapport contient :

- la version du format, l’identifiant camion et un identifiant déterministe de traitement ;
- le nombre de lignes valides/invalides, les timestamps et la cadence observée ;
- les paramètres de filtrage et le seuil, toujours exprimés en m/s² ;
- pour chaque jour UTC : nombre de freinages, score et liste d’événements ;
- pour chaque événement : identifiant, début/fin observés et pics brut/filtré.

Le score est défini pour la démonstration :

```text
score = max(0, 100 - 10 × nombre_de_freinages_du_jour)
```

Il ne couvre que les échantillons fournis. Dix secondes de données ne constituent
pas une preuve de conduite sur une journée entière. La formule et les preuves
acceptables devront être validées avec l’assureur.

## 5. Robustesse, stockage et limites

- Erreurs de fichier, JSON, CSV ou base SQLite : message `[ERREUR CONTRÔLÉE]` et code de sortie 2.
- Ligne CSV invalide : signalement, interruption de l’événement en cours et poursuite sur les autres lignes.
- Si aucune ligne n’est exploitable, aucun nouveau rapport de succès n’est produit.
- Une position GPS invalide ne prouve jamais une sortie de ZFE.
- Les sorties ne peuvent pas écraser les entrées par une erreur de chemin.
- Le JSON est écrit dans un fichier temporaire puis remplacé atomiquement.
- Rejouer le même CSV avec la même configuration ne double pas les événements dans SQLite.
- Les mesures brutes ne sont pas toutes conservées en RAM ni exportées : seules les statistiques et événements sont stockés.

L’intégrité SQLite est transactionnelle pour un traitement de fichier. En cas
d’arrêt avant la fin, le traitement doit être relancé ; il ne s’agit pas encore
d’un service capteur Android avec reprise en milieu de flux.

Le POC ne réalise **ni acquisition GNSS/accéléromètre Android, ni cartographie
européenne complète, ni guidage, ni synchronisation Cloud**. Il ne détermine pas
les droits réels de circulation d’un véhicule. Le polygone est celui, simplifié,
de l’exercice. Une perte GPS en tunnel exige un traitement de position incertaine ;
un calcul hors ligne ne recrée pas un signal satellite absent.

Les hypothèses géométriques sont un polygone simple local sans trou et sans
franchissement de l’antiméridien. La validation topologique d’une cartographie
réelle fait partie d’une étape ultérieure.

## 6. Procédure de test local

```bash
python3 -m unittest discover -s tests -v
```

Les tests couvrent notamment : Bounding Box, concavité, bordures/sommets,
annotation erronée, entrée/sortie, données invalides, conversion G, gravité sur Z,
filtrage, hystérésis, changement de jour, rejeu, erreurs de fichiers et
fonctionnement avec les sockets réseau bloquées.

Validation réalisée : **30 tests réussis sous Python 3.12.14 et Python 3.14.0**.
Le compte rendu est disponible dans `docs/verification.md`.

Pour montrer une erreur contrôlée pendant la démonstration :

```bash
python3 main.py geo --gps fichier_absent.json
```

La sortie 2 est attendue pour ce test. Elle ne doit pas afficher de traceback.

Sur macOS/Linux, un essai synthétique à 100 Hz est disponible :

```bash
python3 tools/benchmark.py --samples 100000
```

Le script génère temporairement 100 000 échantillons, dont un pic de freinage,
puis mesure le temps et la mémoire du processus. Les performances de l’ordinateur
ne constituent pas une validation sur Zebra : le budget de 150 Mo doit être
vérifié sur le matériel cible.

## 7. Organisation du projet

```text
main.py                         commandes demo, geo et safety
gml_edge/geo.py                  géométrie et alertes ZFE
gml_edge/safety.py               filtrage, événements, SQLite et JSON
gml_edge/cli.py                  options et erreurs contrôlées
gml_edge/common.py               validation des données JSON
data/                           jeux de données locaux
tests/                          tests automatisés sans dépendances
tools/benchmark.py              mesure sur flux synthétique
examples/daily_score.json        exemple de rapport produit
runtime/                        résultats locaux, exclus de Git
```

## Références

- [W. Randolph Franklin, principe PNPOLY / Ray Casting](https://wrfranklin.org/Research/Short_Notes/pnpoly.html).
- [Documentation Python : lecture CSV](https://docs.python.org/3/library/csv.html).
- [Documentation Python : SQLite](https://docs.python.org/3/library/sqlite3.html).
