# La gazette

Projet de data engineering (Master 2). Chaque matin, le pipeline collecte les flux RSS de la presse quotidienne régionale (PQR) et nationale (PQN) et stocke les articles bruts. Les étapes suivantes (nettoyage, regroupement par thèmes, édition à 7 h) viendront ensuite.

Ce dépôt contient **le socle, la collecte et le stockage brut**. Les modules `clean/`, `themes/` et `deliver/` sont vides pour l'instant.

## Ce qu'on collecte, et ce qu'on ne collecte pas

- Pour chaque article, on garde **le titre, l'extrait fourni par le flux, le lien et la date**, plus la provenance (source, URL du flux, heure de collecte).
- On ne garde **jamais le texte intégral**. Si un flux ne fournit que `content:encoded`, l'extrait reste vide.
- On ne garde **aucune donnée personnelle** : ni auteurs, ni commentaires, ni profils.
- La collecte est polie :
  - un User-Agent `LaGazette/<version>` avec un e-mail de contact ;
  - une vérification du `robots.txt` avant chaque flux ;
  - des timeouts et 3 essais au plus, avec backoff exponentiel ;
  - une pause entre deux requêtes vers un même domaine ;
  - des GET conditionnels (ETag / Last-Modified).

## Prérequis

- Docker Desktop (ou Docker Engine) avec Compose v2.24 ou plus, et environ 4 Go de RAM libres.
- Pour lancer la collecte et les tests hors Docker : Python 3.12 ou plus, et `make` (sous Windows : `mingw32-make`, Git Bash ou WSL).

## Installation

```bash
cp .env.example .env
# Ouvrir .env et renseigner GAZETTE_CONTACT_EMAIL (e-mail envoyé aux éditeurs dans le User-Agent).
# Sous Linux, mettre aussi AIRFLOW_UID=$(id -u).
```

## Lancer avec Airflow

```bash
docker compose up -d        # ou : make up (construit l'image puis démarre)
```

Ce qui démarre :
- `postgres` : PostgreSQL 16 avec pgvector, et deux bases, `airflow` (métadonnées) et `gazette` (extension `vector`) ;
- `airflow-init` : migrations et compte admin, puis il s'arrête ;
- `airflow-apiserver`, `airflow-scheduler` et `airflow-dag-processor` : Airflow 3.3.2 en LocalExecutor.

L'interface est sur **http://localhost:8080**. Identifiants : `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD` (`airflow` / `airflow` par défaut).

Le DAG **`gazette_daily`** tourne tous les jours à **5 h, heure de Paris**. Il est en pause à sa création. Pour le lancer :
1. Activez-le dans l'interface.
2. Déclenchez-le avec le bouton « Trigger ».

En ligne de commande, l'équivalent est :

```bash
docker compose exec airflow-scheduler airflow dags unpause gazette_daily
docker compose exec airflow-scheduler airflow dags trigger gazette_daily
```

Le DAG contient :
- `collection_date` : la date du jour de collecte (heure de Paris) ;
- `list_sources` : les sources actives de `sources.csv` ;
- `collect` : **une tâche par source** (dynamic task mapping) avec 2 relances. Un refus du `robots.txt` ou une erreur 404 échoue tout de suite, sans relance ;
- `write_manifest` : écrit `_manifest.json`, **même si des sources ont échoué** (`trigger_rule="all_done"`).

Le run du DAG reste « success » quand une source échoue. Les échecs sont visibles sur la tâche mappée concernée et dans le manifeste.

À noter : si vous activez le DAG après 5 h, Airflow lance aussitôt le run de 5 h du jour (`catchup=False` ne rattrape que le dernier créneau).

Autres commandes : `make logs` (journaux) et `make down` (arrêt, les données sont conservées).

## Lancer la collecte sans Airflow

```bash
make install                          # virtualenv .venv + dépendances
make collect                          # = python -m gazette collect
make collect DATE=2026-10-21          # forcer le jour de collecte
python -m gazette collect --source le-monde --source l-obs
```

La commande résume chaque source. Elle renvoie le code 0 si au moins une source a réussi, et 1 si toutes ont échoué. Avec `--strict`, elle renvoie 1 dès qu'une source échoue.

## Stockage brut

```
data/raw/<AAAA-MM-JJ>/          jour de collecte, en heure de Paris
├── _manifest.json              bilan par source : statut, items, erreur, durée
├── pqr/<slug>.json             un fichier par source
└── pqn/<slug>.json
data/state/
├── http_cache.json             ETag / Last-Modified par flux
└── runs/<date>/<slug>.json     résultat de chaque source, sert à construire le manifeste
```

- **Le slug** vient du nom de la source : minuscules, sans accents, mots séparés par des tirets (`La Dépêche du Midi` → `la-depeche-du-midi`).
- **L'identifiant d'un item** est le sha256 de son URL canonique : sans fragment, sans paramètres de suivi `utm_*` ou `xtor`.
- **Idempotence** : relancer la collecte le même jour fusionne les nouveaux items et déduplique par `id`. Chaque passage ajoute une entrée dans `fetches`, et rien n'est écrasé.
- **Écriture atomique** : on écrit dans un fichier temporaire du même dossier, puis on fait `os.replace`. Un verrou protège les mises à jour concurrentes.
- **GET conditionnel** : il n'est utilisé que si le fichier du jour existe déjà. Le premier passage de la journée récupère toujours le flux complet.
- **Statuts du manifeste** : `ok`, `not_modified` (réponse 304) et `failed`. Une source sans résultat, par exemple une tâche tuée, est marquée `failed`.
- **Aucun filtrage** : le stockage brut ne filtre ni l'âge des articles ni le HTML. C'est le rôle de l'étape de nettoyage.

## Sources

Les sources sont décrites dans `sources.csv` (UTF-8, séparateur `;`). Ses colonnes sont `nom`, `type` (PQR/PQN), `region`, `acces` (URL du flux), `conditions`, `notes` et `active`. Passez `active` à `false` pour désactiver une source sans la supprimer.

## Vérifier

```bash
make lint      # ruff check + ruff format --check
make test      # pytest, sans aucun appel réseau (fixtures RSS + httpx.MockTransport)
docker compose config --quiet
```

```bash
# Contenu du jour et absence de doublons
ls data/raw/$(date +%F)/pqr data/raw/$(date +%F)/pqn
python -c "import json,glob; [print(f, len(d['items']), len({i['id'] for i in d['items']})) for f in glob.glob('data/raw/*/*/*.json') for d in [json.load(open(f, encoding='utf-8'))]]"
```

La CI GitHub Actions (`.github/workflows/ci.yml`) enchaîne trois contrôles :
1. ruff, puis pytest sous Python 3.12 ;
2. `docker compose config`, puis la construction de l'image ;
3. un contrôle du DAG dans l'image (`tests/check_dag.py`).

## Arborescence

```
dags/gazette_daily.py        DAG Airflow (aucune logique métier)
src/gazette/config.py        réglages lus depuis l'environnement / .env
src/gazette/sources.py       lecture et validation de sources.csv
src/gazette/collect/         fetcher.py (HTTP poli), parser.py (feedparser → Item), runner.py
src/gazette/storage/         raw.py (stockage par jour, manifeste), atomic.py
src/gazette/cli.py           python -m gazette collect
src/gazette/{clean,themes,deliver}/   étapes suivantes (vides)
db/init/01-init.sql          base gazette + extension vector
tests/                       pytest + fixtures RSS locales
```
