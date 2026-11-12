# Dictionnaire des données — La gazette

Ce document décrit tout ce que la collecte écrit : les **articles bruts** (fichiers JSON) et l'**état de la collecte** (base PostgreSQL `gazette`). Pour chaque champ, il précise son type, son origine dans le flux et ses règles.

Les sections 4 à 8 forment le **contrat des données nettoyées** (TD 4) : tables `articles_propres`, `rejets` et `controles`, garanties, traçabilité et rétention. Les règles de tri sont dans [`qualite.md`](qualite.md).

## Ce qu'on ne stocke jamais

- **Le texte intégral.** Ni `content:encoded` (RSS) ni `<content>` (Atom). Quand un flux ne fournit que le contenu intégral, `resume` reste vide.
- **Les données personnelles.** Ni auteurs (`author`, `dc:creator`, `<author>`), ni contributeurs, ni commentaires, ni profils. Le `payload` est construit par liste blanche, pour qu'aucun champ inattendu ne passe. Au nettoyage, la rubrique Carnet (noms de particuliers) est écartée (`R04_services_locaux`).
- **Plus qu'un extrait court dans les données propres.** `articles_propres.resume` est borné à 300 caractères (contrôle C09 à chaque passage).

## 1. Articles bruts (JSON)

### Emplacement

```
data/articles_bruts/<AAAA-MM-JJ>/<pqr|pqn>/<slug>.json      un fichier par source et par jour
data/articles_bruts/<AAAA-MM-JJ>/_manifest.json             bilan du jour, source par source
```

- **Le jour** est la date de collecte en heure de Paris (`Europe/Paris`).
- **Le sous-dossier** vient de la colonne `type` de la source (`PQR` → `pqr`).
- **Le slug** vient du nom : minuscules, sans accents, mots séparés par des tirets (`La Dépêche du Midi` → `la-depeche-du-midi`).
- **Écriture atomique** : on écrit dans un fichier temporaire, puis on fait `os.replace`.

### En-tête du fichier source

| Champ | Type | Description |
|---|---|---|
| `source` | texte | Nom de la source (= `sources.nom`) |
| `slug` | texte | Identifiant de fichier dérivé du nom |
| `type` | `PQR` \| `PQN` | Presse régionale ou nationale |
| `feed_url` | URL | Flux interrogé (= `sources.url_flux`) |
| `collection_date` | date `AAAA-MM-JJ` | Jour du fichier |
| `fetches` | liste | Un élément par passage de collecte ce jour-là, sans jamais rien écraser |
| `fetches[].fetched_at` | horodatage ISO 8601 | Heure de la requête |
| `fetches[].http_status` | entier | `200` (flux reçu) ou `304` (non modifié) |
| `fetches[].new_items` | entier | Articles écrits lors de ce passage |
| `items` | liste | Les articles, décrits ci-dessous |

### Champs d'un article (`items[]`)

| Champ | Type | Obligatoire | RSS 2.0 | Atom | Règle |
|---|---|---|---|---|---|
| `id` | texte (64 hex) | oui | — | — | `sha256(source + "\n" + lien canonique)`. **Clé de déduplication (source, lien).** Le `guid` / `<id>` du flux n'est pas utilisé. |
| `source` | texte | oui | — | — | Nom de la source (`sources.nom`) |
| `lien` | URL | oui | `<link>` | `<link rel="alternate" href>` | Tel que fourni. Une entrée sans lien est ignorée. |
| `titre` | texte | oui (peut être vide) | `<title>` | `<title>` | Espaces de début et de fin retirés |
| `resume` | texte (HTML possible) | oui (peut être vide) | `<description>` | `<summary>` | Extrait tel que fourni, **non nettoyé**. Vide s'il ne serait qu'une recopie du contenu intégral. |
| `publie_le` | horodatage ISO 8601 ou `null` | non | `<pubDate>` | `<published>`, à défaut `<updated>` | Normalisé en heure de Paris. `null` si absent ou illisible (la valeur brute reste dans `payload`). |
| `recupere_le` | horodatage ISO 8601 | oui | — | — | Heure de la requête qui a ramené l'article |
| `rubrique` | texte ou `null` | non | 1re `<category>` | 1re `<category term>` | Pas de normalisation à ce stade |
| `payload` | objet | oui | — | — | Champs bruts utiles pour rejouer le nettoyage (voir ci-dessous) |

**Lien canonique** (sert seulement à calculer `id`) : schéma et hôte en minuscules, port par défaut retiré, fragment `#…` retiré, paramètres de suivi retirés (`utm_*`, `xtor`, `xts`, `at_medium`, `at_campaign`, `at_source`, `fbclid`).

**`payload`** est construit par liste blanche. Seules ces clés peuvent y figurer, et seulement si le flux les fournit :

| Clé | Contenu |
|---|---|
| `id` | `guid` RSS ou `<id>` Atom (pour information, pas pour l'identité) |
| `title`, `link` | Valeurs brutes |
| `summary` | Extrait brut (même règle que `resume`) |
| `published`, `updated` | Dates brutes, telles qu'écrites dans le flux |
| `links` | `[{href, rel, type}]` |
| `tags` | `[{term, scheme, label}]` (catégories) |

### Déduplication

- **Dans un fichier** : un article n'apparaît qu'une fois par `id`.
- **D'un jour à l'autre** : la table `articles_vus` (section 2.2) retient les `id` déjà écrits. Un article vu hier n'est pas réécrit aujourd'hui.
- **Statistiques** : chaque passage compte les articles **reçus** (entrées du flux), **écrits** et **ignorés** (déjà vus, en double dans le flux, ou sans lien).

### Manifeste du jour (`_manifest.json`)

Le manifeste contient un `summary` et une entrée par source active. Le résumé `summary` compte les sources par statut (`ok`, `not_modified`, `failed`, `sleeping`) et totalise `new_items` et `items_total`.

| Champ d'une entrée | Description |
|---|---|
| `status` | `ok` (200), `not_modified` (304), `failed` (échec), `sleeping` (source en sommeil, non interrogée) |
| `http_status` | Dernier code HTTP, ou `null` |
| `received`, `new_items`, `ignored` | Reçus, écrits et ignorés au dernier passage |
| `items_total` | Articles présents dans le fichier du jour |
| `error` | Message d'erreur ; pour une source en sommeil, la commande de réveil |
| `consecutive_failures` | Valeur de `sources.echecs_consecutifs` après ce passage |
| `started_at`, `duration_s` | Début et durée de la collecte de cette source |

## 2. Base `gazette` (PostgreSQL 16)

Le schéma est créé par des migrations SQL numérotées : `src/gazette/db/migrations/NNN_*.sql`. Elles s'appliquent avec `python -m gazette db migrate`, ce que fait automatiquement le service `airflow-init`. La table `schema_migrations` retient les versions déjà appliquées (`version`, `fichier`, `applique_le`).

### 2.1 `sources` (migration 001)

La table est amorcée depuis `sources.csv` par `python -m gazette db seed`. Ensuite, c'est **la base qui fait foi** :
- un nouvel amorçage met à jour `type` et `url_flux` ;
- il ne touche ni à `actif` ni à l'état de collecte.

| Colonne | Type | Contrainte | Description |
|---|---|---|---|
| `nom` | `text` | clé primaire | Nom de la source |
| `type` | `text` | `PQR` ou `PQN` | Presse régionale ou nationale |
| `url_flux` | `text` | unique, `http(s)://` | Adresse du flux RSS/Atom |
| `actif` | `boolean` | défaut `true` | `false` = désactivée à la main, jamais interrogée |
| `derniere_reussite` | `timestamptz` | null possible | Dernière collecte réussie (200 ou 304) |
| `echecs_consecutifs` | `integer` | ≥ 0, défaut 0 | +1 par collecte en échec, remis à 0 à chaque réussite |
| `etag` | `text` | null possible | `ETag` du dernier 200, renvoyé en `If-None-Match` |
| `last_modified` | `text` | null possible | `Last-Modified` du dernier 200, renvoyé en `If-Modified-Since` |

**Échecs et sommeil :**
- **Comptage** : un échec n'est compté qu'une fois par collecte, au dernier essai Airflow. Une erreur permanente (robots.txt, 404, flux illisible) n'est pas relancée et compte tout de suite.
- **Mise en sommeil** : à **3 échecs consécutifs**, la source n'est plus interrogée.
- **Signalement** : la source apparaît dans la vue `sources_en_sommeil`, avec le statut `sleeping` dans le manifeste, un avertissement à chaque run et l'état « en sommeil » dans `python -m gazette sources list`.
- **Réveil** : `python -m gazette sources wake <slug>` (ou `--all`) remet le compteur à 0.

### 2.2 `articles_vus` (migration 003)

C'est l'index des articles déjà écrits dans `data/articles_bruts`, qui sert à dédupliquer d'un jour à l'autre. Les articles eux-mêmes restent en JSON.

| Colonne | Type | Contrainte | Description |
|---|---|---|---|
| `id` | `text` | clé primaire | Même valeur que `items[].id` |
| `source` | `text` | → `sources.nom` | Source de l'article |
| `premiere_collecte` | `date` | non null | Jour du fichier brut où l'article a été écrit |
| `vu_le` | `timestamptz` | défaut `now()` | Date d'insertion dans l'index |

**Ordre des écritures** : le fichier JSON d'abord, l'index ensuite. Un plantage entre les deux peut, au pire, faire réécrire un article au passage suivant, jamais le perdre.

### 2.3 `sources_en_sommeil` (vue, migration 002)

C'est la vue `SELECT nom, type, url_flux, echecs_consecutifs, derniere_reussite FROM sources WHERE actif AND echecs_consecutifs >= 3`.

## 3. Fichiers d'état hors données

| Fichier | Rôle |
|---|---|
| `data/state/runs/<date>/<slug>.json` | Résultat du dernier passage de chaque source ; sert à construire le manifeste quand les sources sont collectées par des tâches Airflow séparées |
| `data/state/alertes/<date>.json` | Contrôles en échec de la dernière alerte envoyée ce jour-là (une même alerte ne part qu'une fois par jour) |

## 4. Données nettoyées (TD 4)

```
data/articles_bruts/<date>/ ──clean──▶ articles_propres (004)  rejets (005)
                                       data/rapports/qualite-<date>.md|.json
              ──quality_checks──▶      controles (006)
                                       data/metrics/qualite.prom
```

Le nettoyage tourne **après chaque passage de collecte** (toutes les 30 min) et rejoue tout le
jour de collecte. Un article est **soit** dans `articles_propres`, **soit** dans `rejets`,
jamais dans les deux (une seule transaction ; si son verdict change, il change de table).
Toutes les dates sont en `timestamptz` (UTC).

### 4.1 `articles_propres` (migration 004)

| Colonne | Type | Sens |
|---|---|---|
| `id` | `text`, clé primaire | même valeur que `items[].id` du brut |
| `source` | `text` → `sources.nom` | source de l'article |
| `lien` | `text` | lien de l'article, tel que fourni |
| `titre` | `text` | nettoyé (encodage, entités, HTML, espaces) ; non vide : contrôle C02 |
| `resume` | `text` | extrait nettoyé, **≤ 300 caractères** (contrôle C09), éventuellement vide |
| `rubrique` | `text` ou null | rubrique du flux, nettoyée |
| `publie_le` | `timestamptz` ou null | date de publication en UTC ; null si le flux n'en donne pas |
| `recupere_le` | `timestamptz` | heure de la requête qui a ramené l'article |
| `jour_collecte` | `date` | jour du dossier `data/articles_bruts/` |
| `fichier_brut` | `text` | fichier d'origine, relatif à `data/articles_bruts/` (traçabilité) |
| `nettoye_le` | `timestamptz` | dernier passage du nettoyage sur cet article |

### 4.2 `rejets` (migration 005)

Mêmes colonnes de provenance (`id`, `source`, `lien`, `titre`, `publie_le`, `recupere_le`,
`jour_collecte`, `fichier_brut`), plus `regle` (première règle qui a rejeté l'article,
`R01_quiz_jeux` … `R11_contenu_partenaire`), `raison` (ce qui l'a déclenchée) et `rejete_le`.

### 4.3 `controles` (migration 006)

Une ligne par contrôle et par passage : `jour_collecte`, `verifie_le`, `controle`, `gravite`
(`critique` ou `avertissement`), `ok`, `valeur`, `seuil`, `detail`. Voir
[`qualite.md` § 7](qualite.md).

## 5. Garanties

| Garantie | Comment |
|---|---|
| **Idempotence** | Rejouer le nettoyage d'un jour ne crée aucun doublon (clé `id`, `ON CONFLICT … DO UPDATE`) et donne le même rapport : les règles d'âge se calculent par rapport à `recupere_le`. |
| **Atomicité** | Les écritures d'un passage sont dans une seule transaction ; rapports et métriques sont écrits dans un fichier temporaire puis renommés. |
| **Unicité** | Un article n'est qu'une fois dans `articles_propres` (clé `id`, `articles_vus` à la collecte, règle R06, contrôle C10). |
| **Fraîcheur** | Objectif : une édition à 7 h. R05 écarte ce qui a plus de 48 h à la collecte (seuil choisi sur les données réelles, `qualite.md` § 4), C06 le vérifie ; `gazette_qualite_dernier_passage_timestamp_seconds` permettra au TD 8 d'alerter si un passage manque. |
| **Complétude** | Chaque article brut du jour est soit propre, soit rejeté (testé : `entres = gardes + rejetes`). |

## 6. Traçabilité

D'un article propre, on remonte au brut et à la source :

```sql
SELECT id, source, fichier_brut, recupere_le FROM articles_propres WHERE lien = 'https://…';
```

```bash
# l'article brut correspondant, tel que publié par l'éditeur (payload compris)
python -c "import json,sys; d=json.load(open('data/articles_bruts/'+sys.argv[1],encoding='utf-8')); print([i for i in d['items'] if i['id']==sys.argv[2]])" <fichier_brut> <id>
```

`source` mène à la table `sources` et à `sources.csv` (conditions d'utilisation). Pour un
rejet, `regle` et `raison` disent pourquoi.

## 7. Rétention

Durées écrites dans `Retention` ([`src/gazette/clean/quality.py`](../src/gazette/clean/quality.py)),
appliquées à chaque passage par la tâche `purge` (`python -m gazette purge --dry-run` pour simuler).

| Donnée | Durée | Pourquoi |
|---|---|---|
| Brut `data/articles_bruts/<date>/`, `data/state/runs/<date>/`, `data/state/alertes/` | **90 jours** | rejouer un nettoyage ou un regroupement sans recontacter les éditeurs |
| `rejets` | **30 jours** | sert à régler les règles ; ensuite, plus rien à en tirer |
| `articles_propres`, `controles` | **365 jours** | historique des thèmes (TD 5-6) et de la qualité |
| Rapports `data/rapports/` | **365 jours** | quelques Ko par jour ; ce sont les preuves d'exploitation |
| `articles_vus` | non purgée | quelques octets par article ; la purger ferait réécrire de vieux articles |
| Éditions publiées (TD 7) | en ligne **30 jours**, puis **stockage froid** | prévu : archive compressée mensuelle, puis le stockage objet de l'infrastructure fournie ; à mettre en œuvre au TD 7 |

## 8. Choix techniques du TD 4

| Besoin | Retenu | Alternatives écartées |
|---|---|---|
| Stockage propre | **PostgreSQL**, même base que `sources` (les vecteurs du TD 5 iront à côté, pgvector) | SQLite : pas d'accès concurrent propre depuis plusieurs tâches Airflow · Parquet : pas de requêtes ad hoc sur les rejets, pas de transactions |
| Schéma | **migrations numérotées 004 à 006**, comme les tables de la collecte | tables créées au démarrage : ne gère pas l'évolution d'une colonne · Alembic : un outil de plus pour quelques fichiers SQL |
| Rapport | **Markdown + JSON** (lisible sur GitHub, relisible par programme) | HTML : plus lourd · seulement en base : illisible sans requête |
| Métriques | **fichier texte au format Prometheus** (rien à faire tourner aujourd'hui ; le TD 8 le collecte avec le *textfile collector* de node_exporter) | StatsD d'Airflow : métriques d'orchestration, pas métier · Pushgateway : un service de plus dès aujourd'hui |
