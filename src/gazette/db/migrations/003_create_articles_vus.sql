-- 003 : index des articles déjà collectés, pour dédupliquer d'un jour à l'autre.
-- Les articles eux-mêmes restent en JSON (data/raw/) ; ici, seulement la clé.
-- id = sha256(source + lien canonique), cf. src/gazette/collect/parser.py:item_id.
CREATE TABLE articles_vus (
    id                 text        PRIMARY KEY,
    source             text        NOT NULL REFERENCES sources (nom) ON UPDATE CASCADE,
    premiere_collecte  date        NOT NULL,
    vu_le              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX articles_vus_source_idx ON articles_vus (source, premiere_collecte);

COMMENT ON TABLE articles_vus IS 'Clés des articles déjà écrits dans data/raw (dédup multi-jours)';
COMMENT ON COLUMN articles_vus.premiere_collecte IS 'Jour du fichier brut où l''article a été écrit';
