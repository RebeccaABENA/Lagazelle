# La gazette

Projet de data engineering (Master 2). Toutes les 30 minutes, le pipeline collecte les flux RSS et Atom de la presse quotidienne régionale (PQR) et nationale (PQN), puis stocke les articles bruts en JSON. Les étapes suivantes (nettoyage, regroupement par thèmes, édition à 7 h) viendront ensuite.

Ce dépôt contient **le socle, la collecte et le stockage brut**. Les modules `clean/`, `themes/` et `deliver/` sont vides pour l'instant. Les champs stockés sont décrits dans [`docs/donnees.md`](docs/donnees.md).

## Ce qu'on collecte, et ce qu'on ne collecte pas

- Pour chaque article, on garde `id, source, lien, titre, resume, publie_le, recupere_le, rubrique, payload`.
- On ne garde **jamais le texte intégral**.
- On ne garde **aucune donnée personnelle** : ni auteurs, ni commentaires, ni profils.
- La collecte est polie :
  - un User-Agent `LaGazette/<version>` avec un e-mail de contact ;
  - une vérification du `robots.txt` avant chaque flux ;
  - des timeouts et 3 essais au plus, avec backoff exponentiel ;
  - une pause entre deux requêtes vers un même domaine ;
  - des GET conditionnels (`ETag` / `Last-Modified`, mémorisés en base) ;
  - **une seule source interrogée à la fois** (pool Airflow à 1 place).

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
- `postgres` : PostgreSQL 16 avec pgvector, et deux bases, `airflow` (métadonnées) et `gazette` (extension `vector`). Il est exposé sur `localhost:5433` pour la collecte hors Docker.
- `airflow-init` fait les migrations Airflow, crée le compte admin, applique **les migrations de la base `gazette`**, amorce la table `sources` depuis `sources.csv` et crée le pool `gazette_editeurs`. Il s'arrête ensuite.
- `airflow-apiserver`, `airflow-scheduler` et `airflow-dag-processor` : Airflow 3.3.2 en LocalExecutor. Le scheduler et le dag-processor attendent que l'api-server soit `healthy`, ce qui peut prendre plusieurs minutes au premier démarrage sur une machine de 8 Go.

L'interface est sur **http://localhost:8080**. Identifiants : `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD` (`airflow` / `airflow` par défaut).

Le DAG **`gazette_daily`** tourne **toutes les 30 minutes**, en heure de Paris, sans intervention. Il est en pause à sa création : activez-le une fois dans l'interface, ou avec cette commande :

```bash
docker compose exec airflow-scheduler airflow dags unpause gazette_daily
```

Le nom est hérité de la première version, où il tournait une fois par jour. Le DAG contient :
- `collection_date` : le jour de collecte (heure de Paris), qui détermine le dossier `data/raw/<date>/` ;
- `list_sources` : les sources **actives et pas en sommeil**, lues dans la table `sources` ;
- `collect` : une tâche par source (dynamic task mapping), dans le pool `gazette_editeurs` à 1 place. Chaque tâche a 2 relances. Une erreur permanente (robots.txt, 404, flux illisible) échoue tout de suite, sans relance ;
- `write_manifest` : écrit `_manifest.json`, **même si des sources ont échoué** (`trigger_rule="all_done"`).

Garde-fous : `catchup=False`, `max_active_runs=1` et `dagrun_timeout` de 25 min, pour que les runs ne se chevauchent jamais.

## Sources, échecs et mise en sommeil

`sources.csv` sert seulement à **amorcer** la table `sources`. Ensuite, c'est la base qui fait foi : un nouvel amorçage met à jour `type` et `url_flux`, mais ne touche pas à `actif` ni aux compteurs.

```bash
python -m gazette db migrate                # migrations SQL numérotées (src/gazette/db/migrations/)
python -m gazette db seed                   # amorçage / mise à jour depuis sources.csv
python -m gazette sources list              # état : actif, en sommeil, désactivé ; échecs ; dernière réussite
python -m gazette sources wake ouest-france # réveille une source en sommeil (ou --all)
```

- **Compteur** : chaque collecte en échec incrémente `echecs_consecutifs`, une seule fois par run même avec les relances. Une réussite le remet à 0.
- **Mise en sommeil** : à **3 échecs consécutifs**, la source n'est plus interrogée.
- **Comment s'en rendre compte** :
  - un journal `WARNING` à chaque run ;
  - le statut `sleeping` dans `_manifest.json`, avec la commande de réveil ;
  - la vue SQL `sources_en_sommeil` ;
  - la commande `sources list`.
- **Réveil** : `sources wake <slug>`. Dans Docker, préfixez la commande par `docker compose exec airflow-scheduler`.
- **Désactiver une source à la main** : `UPDATE sources SET actif = false WHERE nom = '…'`.

## Lancer la collecte sans Airflow

La pile Docker doit tourner, au moins `postgres`, pour la table `sources`. Ensuite :

```bash
make install                          # virtualenv .venv + dépendances
make collect                          # = python -m gazette collect
make collect DATE=2026-10-21          # forcer le jour de collecte
python -m gazette collect --source le-monde --source l-obs
```

La commande renvoie le code 0 si au moins une source a réussi, et 1 si toutes ont échoué. Avec `--strict`, elle renvoie 1 dès qu'une source échoue.

Chaque source produit **une ligne de journal par passage** :

```
source=le-monde statut=ok http=200 recus=16 ecrits=3 ignores=13 duree=0.42s
source=sud-ouest statut=failed http=503 recus=0 ecrits=0 ignores=0 duree=7.10s erreur_type=transitoire echecs_consecutifs=1 erreur="FetchError: HTTP 503 après 3 essais"
```

## Stockage brut

```
data/raw/<AAAA-MM-JJ>/          jour de collecte, en heure de Paris
├── _manifest.json              bilan par source : statut, reçus/écrits/ignorés, erreur, durée
├── pqr/<slug>.json             un fichier par source ; chaque passage y ajoute une entrée « fetches »
└── pqn/<slug>.json
```

- **Clé d'un article** : `id = sha256(source + lien canonique)`. Le `guid` du flux n'est pas utilisé.
- **Déduplication sur plusieurs jours** : la table `articles_vus` retient les `id` déjà écrits. Un article vu hier n'est pas réécrit aujourd'hui. Les articles eux-mêmes restent en JSON.
- **Écriture atomique** : on écrit dans un fichier temporaire, puis on fait `os.replace`.
- **Aucun filtrage** : le stockage brut ne filtre ni l'âge des articles ni le HTML. C'est le rôle de l'étape de nettoyage.

Le détail de chaque champ est dans [`docs/donnees.md`](docs/donnees.md).

## Vérifier

```bash
make lint      # ruff check + ruff format --check
make test      # pytest, sans accès à Internet
```

- **Sans base** : les tests utilisent des fixtures RSS et Atom locales, `httpx.MockTransport` et un vrai `http.server` local, qui sert notamment une source en panne (503).
- **Avec base** : les tests de `tests/test_db.py` (migrations, table `sources`, index `articles_vus`) ont besoin d'un PostgreSQL. Sans lui, ils sont ignorés :

  ```bash
  GAZETTE_TEST_DATABASE_URL=postgresql://airflow:airflow@localhost:5433/gazette make test
  ```

  Chaque test travaille dans un schéma jetable, supprimé à la fin.

La CI GitHub Actions (`.github/workflows/ci.yml`) tourne sur toutes les branches et enchaîne :
1. ruff, puis pytest sous Python 3.12, avec un service PostgreSQL pour les tests de base ;
2. `docker compose config`, puis la construction de l'image ;
3. `airflow-init`, qui applique les migrations et amorce la table `sources` ;
4. un contrôle du DAG dans l'image (`tests/check_dag.py` : fréquence, pool, relances, `all_done`).

## Arborescence

```
dags/gazette_daily.py              DAG Airflow (aucune logique métier)
src/gazette/config.py              réglages lus depuis l'environnement / .env
src/gazette/sources.py             modèle Source, lecture de sources.csv (amorçage)
src/gazette/db/                    connexion, migrate.py, repository.py, migrations/NNN_*.sql
src/gazette/collect/               fetcher.py (HTTP poli), parser.py (RSS/Atom → articles), runner.py
src/gazette/storage/               raw.py (stockage par jour, manifeste), atomic.py
src/gazette/cli.py                 python -m gazette collect | db | sources
src/gazette/{clean,themes,deliver}/   étapes suivantes (vides)
db/init/01-init.sql                création de la base gazette + extension vector
docs/donnees.md                    dictionnaire des données
tests/                             pytest + fixtures RSS/Atom locales
```
