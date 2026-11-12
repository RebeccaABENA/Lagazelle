-- 005 : articles écartés par une règle d'exclusion, avec la règle et la raison.
-- Un article est soit dans articles_propres, soit dans rejets, jamais dans les deux :
-- le nettoyage déplace l'article de l'une à l'autre si le verdict change (autre seuil, autre règle).
CREATE TABLE rejets (
    id             text        PRIMARY KEY,
    source         text        NOT NULL REFERENCES sources (nom) ON UPDATE CASCADE,
    regle          text        NOT NULL,
    raison         text        NOT NULL,
    lien           text        NOT NULL,
    titre          text        NOT NULL,
    publie_le      timestamptz,
    recupere_le    timestamptz NOT NULL,
    jour_collecte  date        NOT NULL,
    fichier_brut   text        NOT NULL,
    rejete_le      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX rejets_regle_idx ON rejets (regle, jour_collecte);
CREATE INDEX rejets_jour_idx ON rejets (jour_collecte, source);

COMMENT ON TABLE rejets IS 'Articles écartés par une règle d''exclusion (R01_quiz_jeux, R02_bons_plans...)';
COMMENT ON COLUMN rejets.id IS 'Même valeur que items[].id dans le fichier brut';
COMMENT ON COLUMN rejets.regle IS 'Identifiant de la première règle qui a rejeté l''article';
COMMENT ON COLUMN rejets.raison IS 'Ce qui a déclenché la règle (motif trouvé, âge, article proche...)';
COMMENT ON COLUMN rejets.fichier_brut IS 'Fichier brut d''origine, relatif à data/articles_bruts/';
