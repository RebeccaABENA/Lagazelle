# Qualité des données — La gazette

> Brouillon du **2026-11-12**, établi sur la collecte du jour : **485 articles de 7 sources** (Ouest-France est exclue : son `robots.txt` interdit l'accès au flux). Tous les chiffres viennent des fichiers `data/articles_bruts/`. Les règles de la section 4 sont des **propositions**, pas encore implémentées.

## 1. Inventaire du brut

| Source | Articles | Avec rubrique | Extrait médian (car.) | > 24 h | > 48 h | > 7 jours |
|---|---:|---:|---:|---:|---:|---:|
| L'Obs | 239 | 234 | 223 | 211 | 177 | 108 |
| La Dépêche du Midi | 115 | 115 | 217 | 5 | 2 | 2 |
| Libération | 50 | 0 | 215 | 0 | 0 | 0 |
| Le Dauphiné Libéré | 25 | 25 | 207 | 0 | 0 | 0 |
| Le Figaro | 20 | 20 | 237 | 0 | 0 | 0 |
| Sud Ouest | 20 | 0 | 175 | 0 | 0 | 0 |
| Le Monde | 16 | 0 | 249 | 1 | 0 | 0 |
| **Total** | **485** | 394 | | **217** | **179** | **110** |

Ce qu'on retient :
- **La forme est plutôt propre** : aucun HTML dans les titres ni les extraits, aucun encodage cassé (`Ã©`), aucun titre vide, aucune date future, aucun doublon exact de titre.
- **Le vrai problème est le contenu** : quiz, promotions, directs, avis de décès, et surtout des articles **anciens** (44 % des articles ont plus de 24 h).
- **L'Obs n'est pas un flux d'actualité** : 288 entrées reçues, dont des articles de mai 2026. Sur ses 239 articles écrits, 211 ont plus de 24 h (88 %).
- **Le Monde, Libération et Sud Ouest ne renseignent aucune rubrique**. On peut la déduire du premier segment de l'URL (`/politique/`, `/economie/`…).

## 2. Dix items qui n'ont rien à faire dans une gazette

| # | Source | Titre | Pourquoi | Règle | id (8 car.) |
|---:|---|---|---|---|---|
| 1 | L'Obs | Quiz. COP31 : 12 questions sur le climat avant d’entamer les négociations | Quiz : un jeu, pas une information | `R01_quiz_jeux` | `61434e5a` |
| 2 | L'Obs | Quiz. 12 questions sur Charles de Gaulle pour tester votre culture Général | Quiz : un jeu, pas une information | `R01_quiz_jeux` | `e835dc9e` |
| 3 | L'Obs | Quiz. Goncourt 2026 : quinze questions sur le prix le plus renommé de la litt… | Quiz : un jeu, pas une information | `R01_quiz_jeux` | `5a7c1fb2` |
| 4 | Le Figaro | Amazon vide ses stocks Nintendo Switch 2 : des consoles, manettes et jeux à p… | Promotion commerciale (rubrique « Bons plans ») | `R02_bons_plans` | `1cc6e412` |
| 5 | Le Figaro | Black Friday : les billets d’avion en promo, c’est dès le 11 novembre (mais p… | Promotion commerciale (rubrique « Bons plans ») | `R02_bons_plans` | `df59c0c0` |
| 6 | Le Monde | EN DIRECT, présidentielle 2027 \| Suivez le grand oral de huit candidats sur l… | Page de direct ou de replay, qui évolue en continu | `R03_direct_replay` | `35af7a98` |
| 7 | La Dépêche du Midi | DIRECT. Présidentielle 2027 : à peine candidat, Teddy Riner est déjà rattrapé… | Page de direct ou de replay, qui évolue en continu | `R03_direct_replay` | `ea66579f` |
| 8 | La Dépêche du Midi | Rafales de vent jusqu'à 70 km/h, pluies... Avec le passage de la tempête Char… | Service pratique local (météo, circulation) | `R04_services_locaux` | `463539cc` |
| 9 | La Dépêche du Midi | "La montagne, ton terrain de jeux, t’a reprise bien trop tôt", Véronique Doye… | Avis de décès d'une personne nommée (donnée personnelle) | `R04_services_locaux` | `8b7b41b5` |
| 10 | La Dépêche du Midi | La Dépêche du Midi lance son espace jeux : à vos neurones, prêts, jouez ! | Publié il y a plus d'un an : n'est pas de l'actualité | `R05_trop_ancien` | `8cb659bd` |

Le préfixe d'`id` permet de retrouver l'article dans les fichiers bruts. Deux doublons approximatifs repérés en plus, c'est-à-dire deux titres pour **un même événement** : la liquidation judiciaire de Fibre Excellence à Saint-Gaudens (Libération et La Dépêche du Midi, similarité 0,75) et l'émissaire britannique pour la sécurité climatique (Sud Ouest et Le Monde, 0,73). Similarité : Jaccard sur les mots du titre.

## 3. Défauts de forme (à corriger par la normalisation, pas à écarter)

| Défaut | Constat | Traitement prévu |
|---|---|---|
| Espaces insécables (`U+00A0`, `U+202F`) avant `:`, `?`, `»` | 307 articles concernés | remplacés par une espace normale |
| Doubles espaces | 8 articles | espaces réduites |
| Entité HTML dans un extrait (`&quot;`) | 1 article (Le Dauphiné Libéré) | décodée |
| Encodage cassé (`Ã©`, `â€`, `�`) | aucun dans les données | réparé par la normalisation (pas de règle d'exclusion) |
| Extrait coupé à ~220 caractères et terminé par `…` | 101 sur 115 pour La Dépêche du Midi | gardé tel quel, signalé dans le rapport |
| Extrait trop long | 47 articles de plus de 300 caractères, maximum 902 (L'Obs) | bornée à 300 caractères, coupée entre deux mots et terminée par `…` |
| Extrait vide | 1 article (Sud Ouest) | gardé, signalé (le titre suffit) |
| Dates | `+01:00` ou `+02:00` selon la saison, formats bruts variés (`+0100`, `GMT`) | converties en UTC (`publie_le` et `recupere_le`) ; la valeur d'origine reste dans `payload` |

## 4. Règles d'exclusion proposées

Un identifiant par règle, pour que le rapport de qualité puisse compter les rejets règle par règle.

| Règle | Ce qu'elle écarte | Pourquoi | Exemple réel | Écartés (sur 485) |
|---|---|---|---|---:|
| `R01_quiz_jeux` | rubrique « Les jeux du Nouvel Obs », ou titre commençant par « Quiz » | un jeu n'est pas une information | « Quiz. COP31 : 12 questions sur le climat avant d’entamer le… » | 12 |
| `R02_bons_plans` | rubrique ou section d'URL `bons-plans` | promotions commerciales, souvent des liens d'affiliation | « Amazon vide ses stocks Nintendo Switch 2 : des consoles, ma… » | 11 |
| `R03_direct_replay` | titre commençant par `EN DIRECT`, `DIRECT` ou `REPLAY` | page qui évolue en continu, sans contenu stable | « EN DIRECT, présidentielle 2027 \| Suivez le grand oral de hu… » | 7 |
| `R04_services_locaux` | rubriques Météo, Circulation, Carnet noir | service pratique, et noms de personnes dans le carnet | « Rafales de vent jusqu'à 70 km/h, pluies... Avec le passage… » | 3 |
| `R05_trop_ancien` | article publié il y a plus de **N heures** (seuil dans la configuration, 48 h par défaut) | une gazette du jour ne reprend pas l'ancien | « La Dépêche du Midi lance son espace jeux : à vos neuro… » (2025) | 204 à 24 h, 166 à 48 h, 100 à 7 jours |
| `R06_doublon_approx` | titre dont la similarité (Jaccard sur les mots, seuil 0,7, titres d'au moins 4 mots) dépasse le seuil avec un article déjà gardé : le plus ancien est gardé | même événement repris par deux titres | Fibre Excellence (Libération / La Dépêche) ; émissaire britannique pour la sécurité climatique (Sud Ouest / Le Monde) | 2 |
| `R07_horoscope` | titre, rubrique ou section d'URL contenant le mot `horoscope` ou `astrologie` (mot entier) | prévision astrale : ni information ni actualité | *« Horoscope du jeudi 12 novembre : Bélier, Taureau… » (exemple type, aucun dans les données, testé sur fixture)* | 0 |
| `R08_date_future` | date de publication postérieure à la collecte | date fausse | *aucune dans les données, testée sur fixture* | 0 |
| `R09_programme_tv` | titre, rubrique ou section d'URL annonçant un programme télé (« programme TV », « ce soir à la télé », `/programme-tv/`) | grille de programmes : un service, pas une information | *« Programme TV du jeudi 12 novembre : que regarder ce soir ? » (exemple type, aucun dans les données, testé sur fixture)* | 0 |
| `R10_publi_reportage` | « publi-reportage », « advertorial », « contenu sponsorisé », « article sponsorisé », section d'URL `/publi-reportage/` | publicité présentée comme un article | *« Publi-reportage : comment bien choisir sa mutuelle » (exemple type, aucun dans les données, testé sur fixture)* | 0 |
| `R11_contenu_partenaire` | « contenu partenaire », « contenu de marque », « brand content », section d'URL `/partenaire/` | contenu fourni par un annonceur, droits de republication non garantis | *« Contenu partenaire : les assurances en 2027 » (exemple type, aucun dans les données, testé sur fixture)* | 0 |

**Seuil d'âge (R05).** Il faut le choisir avec le garde-fou « le taux de rejet d'une source ne dépasse pas 80 % » : pour L'Obs, un seuil de 24 h rejetterait 88 % de ses articles (alerte), 48 h 74 %, 7 jours 45 %.

**Comptage.** Dans le rapport, un article n'est compté que pour la **première** règle qui le rejette (dans l'ordre R01, R02…). C'est pourquoi `R05_trop_ancien` compte moins d'articles que le nombre d'articles anciens : 13 quiz ou directs anciens sont comptés dans `R01_quiz_jeux` et `R03_direct_replay`. Avec le seuil de 48 h, 201 articles sont écartés sur 485 (41 %).

## 5. Ce qu'on n'a pas trouvé

Dans cette collecte, il n'y a **aucun** horoscope, programme TV, publi-reportage ou contenu partenaire déclaré, titre vide, date future ou doublon exact. Les « critiques TéléObs » de L'Obs (17 articles) sont des critiques de séries, pas des programmes : on les garde. Ces cas restent couverts par des règles préventives (`R07_horoscope`, `R09_programme_tv`, `R10_publi_reportage` et `R11_contenu_partenaire`) et des tests sur fixtures, au cas où un flux les produirait un jour. Un titre vide n'a pas de règle : il serait détecté par le contrôle de données « aucun titre vide » (étape 4 du TD).

## 6. Point de vigilance : données personnelles

Les rubriques Justice, Faits divers et Accidents (42 articles ici) citent parfois des personnes nommées dans le titre. Les règles de ce document n'y touchent pas. Il faudra décider, avec la conformité du TD 4, ce qu'on garde (nom, initiales) et l'écrire dans `donnees.md`.

## 7. Contrôles de qualité (après chaque passage)

Les règles écartent ; les contrôles **vérifient des faits sur les vraies données**, relues
dans `articles_propres` et `rejets` (même `CleanStore` que le nettoyage) juste après lui.
Ils ne testent pas du code (la CI le fait sur fixture) : ils tournent en exploitation, dans la
tâche Airflow `quality_checks`, à **chaque passage du DAG (toutes les 30 min)** :
`collect → write_manifest → clean → quality_checks → purge`.
Code : [`checks.py`](../src/gazette/clean/checks.py) et [`quality.py`](../src/gazette/clean/quality.py) ;
seuils : `ChecksConfig`, qui reprend ceux de `RulesConfig` quand ils se recoupent.

| Contrôle | Fait vérifié | Gravité |
|---|---|---|
| `C01_dates_futures` | aucun article publié après sa collecte (+ 10 min, comme R08) | critique |
| `C02_titres_vides` | aucun titre vide (aucune règle ne les écarte, § 5) | critique |
| `C03_taux_rejet_source` | le taux de rejet de chaque source (≥ 10 articles) ne dépasse pas 80 % | avertissement |
| `C04_volume` | au moins 30 articles propres dans la journée, jugé à partir de 6 h | critique |
| `C05_couverture_sources` | chaque source présente dans le brut a au moins un article gardé | avertissement |
| `C06_fraicheur` | aucun article de plus de 48 h à la collecte (comme R05 ; date absente acceptée) | critique |
| `C07_html_residuel` | aucune balise ni entité HTML dans les titres et extraits | critique |
| `C08_encodage` | aucun encodage cassé, réparable (oublié) ou perdu (`�`) | critique |
| `C09_extrait_borne` | aucun extrait de plus de 300 caractères (jamais de texte intégral) | critique |
| `C10_titres_uniques` | aucun titre en double (titre replié) parmi les articles du jour | critique |
| `C11_collecte` | aucune source en échec dans le manifeste de collecte | avertissement |

- **Critique** : la donnée est fausse ; la tâche échoue (rouge dans Airflow) **après** avoir
  alerté. Au TD 7, la livraison de l'édition dépendra de ce résultat.
- **Avertissement** : quelque chose dérive ; on alerte, la tâche réussit.
- **C03 et le seuil de R05** : à 48 h, L'Obs est rejeté à 74 % (§ 4), juste sous 80 %. Si
  C03 sonne pour L'Obs, c'est le signe attendu que ce flux n'est pas un flux d'actualité : on
  le note dans le journal (§ 9), on ne baisse pas le garde-fou.
- **Contrôles et « Règles à surveiller »** : la section du rapport (§ 8) signale une règle
  qui écarte plus de 50 % d'une source ; C03 regarde le taux de rejet total d'une source,
  toutes règles confondues, et alerte.

**Où vont les chiffres :** le résumé part en XCom de la tâche `quality_checks` (visible dans
Airflow) ; l'historique dans la table `controles` (migration 006) ; les métriques dans
`data/metrics/qualite.prom` (format texte Prometheus, que le TD 8 viendra collecter) ; le
tableau des contrôles est ajouté à la fin du rapport du jour.

### L'alerte

Un webhook Discord ou Slack (`GAZETTE_ALERT_WEBHOOK_URL` dans `.env`, jamais commité), envoyé
dès qu'un contrôle échoue. Le DAG passant toutes les 30 min, **une même alerte ne part qu'une
fois par jour** : on réalerte seulement si la liste des contrôles en échec change (état dans
`data/state/alertes/<date>.json`).

**La déclencher pour de vrai :**
1. créer le webhook sur le salon de l'équipe, coller l'URL dans `.env`, `docker compose up -d` ;
2. tester le canal : `python -m gazette alert-test` ;
3. faire échouer un contrôle : par exemple lancer `python -m gazette check` sur un jour peu
   rempli après 6 h (C04), ou supprimer `data/state/alertes/<date>.json` pour renvoyer une
   alerte déjà partie ;
4. noter l'exercice ci-dessous, avec une capture.

| Date | Déclencheur | Reçue sur | Délai | Capture |
|---|---|---|---|---|
| AAAA-MM-JJ | … | … | … | … |

### Choix de l'outil de contrôle

| Option | Pour | Contre | Choix |
|---|---|---|---|
| **Assertions maison** (11 fonctions sur les lignes relues en base) | ~250 lignes, aucune dépendance, testées sur fixture, résultats dans notre format (rapport, table, métriques) | l'historique et le rendu sont à écrire soi-même | ✅ retenu |
| Great Expectations | attentes standard, « data docs » HTML | lourd (contexte, stores, checkpoints), dépendance importante à faire cohabiter avec les contraintes d'Airflow ; surdimensionné pour 11 faits | ✗ |
| Soda Core (SodaCL) | contrôles déclaratifs en YAML/SQL | une syntaxe de plus ; alertes et métriques passent par Soda Cloud (payant), budget nul | ✗ |

### Une alerte par jour et par échec

| Option | Pour | Contre | Choix |
|---|---|---|---|
| **Une alerte par jour, tant que la liste des échecs ne change pas** | on est prévenu une fois, et de nouveau si la situation change | un petit fichier d'état (purgé avec le brut) | ✅ retenu |
| Une alerte à chaque passage | simple | 48 messages par jour : plus personne ne les lit | ✗ |
| Pas d'alerte, seulement le rapport | rien à configurer | personne ne le voit si personne ne l'ouvre | ✗ |

## 8. Le rapport de qualité

Chaque passage du nettoyage réécrit `data/rapports/qualite-AAAA-MM-JJ.md` et `.json`
([`report.py`](../src/gazette/clean/report.py)) ; le dernier passage du jour est le bilan
complet. Il donne les entrés, gardés et rejetés, les rejets par règle (toutes, même à 0) et par
source, les cinq rejets les plus fréquents avec un exemple, et les **règles à surveiller**
(une règle qui écarte au moins 50 % d'une source d'au moins 5 articles). Les contrôles y
ajoutent leur tableau ✅ / ❌ (section « Contrôles de qualité »). Rapports gardés 365 jours.

Pour lire les exemples d'une règle qui mord trop :

```sql
SELECT source, titre, raison, lien FROM rejets
WHERE jour_collecte = CURRENT_DATE AND regle = 'R05_trop_ancien' ORDER BY source;
```

## 9. Journal des ajustements des règles

Pour la prochaine séance : lire le rapport **trois matins de suite** et ajuster au moins une
règle (`RulesConfig` ou les motifs de `rules.py`) ; chaque changement est noté ici.

| Date | Règle | Constat (chiffres du rapport) | Changement | Effet observé |
|---|---|---|---|---|
| | | | | |
