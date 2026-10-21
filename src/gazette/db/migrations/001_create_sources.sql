-- 001 : sources de presse et état de leur collecte.
-- Amorcée depuis sources.csv par `python -m gazette db seed`.
CREATE TABLE sources (
    nom                 text        PRIMARY KEY,
    type                text        NOT NULL CHECK (type IN ('PQR', 'PQN')),
    url_flux            text        NOT NULL UNIQUE CHECK (url_flux ~ '^https?://'),
    actif               boolean     NOT NULL DEFAULT true,
    derniere_reussite   timestamptz,
    echecs_consecutifs  integer     NOT NULL DEFAULT 0 CHECK (echecs_consecutifs >= 0),
    etag                text,
    last_modified       text
);

COMMENT ON TABLE sources IS 'Flux RSS/Atom collectés par La gazette';
COMMENT ON COLUMN sources.actif IS 'false = source désactivée à la main';
COMMENT ON COLUMN sources.derniere_reussite IS 'Dernière collecte réussie (200 ou 304)';
COMMENT ON COLUMN sources.echecs_consecutifs IS 'Remis à 0 à chaque réussite';
COMMENT ON COLUMN sources.etag IS 'En-tête ETag du dernier 200, renvoyé en If-None-Match';
COMMENT ON COLUMN sources.last_modified IS 'En-tête Last-Modified du dernier 200, renvoyé en If-Modified-Since';
