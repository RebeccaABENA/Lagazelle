-- 004 : articles propres, c'est-à-dire normalisés (texte, dates en UTC, extrait borné)
-- et acceptés par les règles d'exclusion (src/gazette/clean/rules.py).
-- Alimentée à chaque passage du nettoyage : `id` est celui de l'article brut
-- (sha256 de source + lien canonique), ce qui donne la traçabilité jusqu'au fichier brut.
CREATE TABLE articles_propres (
    id             text        PRIMARY KEY,
    source         text        NOT NULL REFERENCES sources (nom) ON UPDATE CASCADE,
    lien           text        NOT NULL,
    titre          text        NOT NULL,
    resume         text        NOT NULL,
    rubrique       text,
    publie_le      timestamptz,
    recupere_le    timestamptz NOT NULL,
    jour_collecte  date        NOT NULL,
    fichier_brut   text        NOT NULL,
    nettoye_le     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX articles_propres_jour_idx ON articles_propres (jour_collecte, source);
CREATE INDEX articles_propres_publie_idx ON articles_propres (publie_le);

COMMENT ON TABLE articles_propres IS 'Articles normalisés et acceptés par les règles d''exclusion';
COMMENT ON COLUMN articles_propres.id IS 'Même valeur que items[].id dans le fichier brut';
COMMENT ON COLUMN articles_propres.publie_le IS 'Date de publication en UTC ; null si le flux n''en donne pas';
COMMENT ON COLUMN articles_propres.recupere_le IS 'Heure de la requête qui a ramené l''article (UTC)';
COMMENT ON COLUMN articles_propres.jour_collecte IS 'Jour (heure de Paris) du dossier data/articles_bruts/ où l''article a été écrit';
COMMENT ON COLUMN articles_propres.fichier_brut IS 'Fichier brut d''origine, relatif à data/articles_bruts/ (ex. 2026-11-12/pqr/sud-ouest.json)';
COMMENT ON COLUMN articles_propres.nettoye_le IS 'Dernier passage du nettoyage sur cet article';
