# Dictionnaire des données — La gazette

Ce document décrit tout ce que la collecte écrit : les **articles bruts** (fichiers JSON) et l'**état de la collecte** (base PostgreSQL `gazette`). Pour chaque champ, il précise son type, son origine dans le flux et ses règles.

## Ce qu'on ne stocke jamais

- **Le texte intégral.** Ni `content:encoded` (RSS) ni `<content>` (Atom). Quand un flux ne fournit que le contenu intégral, `resume` reste vide.
- **Les données personnelles.** Ni auteurs (`author`, `dc:creator`, `<author>`), ni contributeurs, ni commentaires, ni profils. Le `payload` est construit par liste blanche, pour qu'aucun champ inattendu ne passe.

## 1. Articles bruts (JSON)

### Emplacement

```
data/raw/<AAAA-MM-JJ>/<pqr|pqn>/<slug>.json      un fichier par source et par jour
data/raw/<AAAA-MM-JJ>/_manifest.json             bilan du jour, source par source
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

C'est l'index des articles déjà écrits dans `data/raw`, qui sert à dédupliquer d'un jour à l'autre. Les articles eux-mêmes restent en JSON.

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
