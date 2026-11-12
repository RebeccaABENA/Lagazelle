-- 006 : historique des contrôles de qualité (TD 4), une ligne par contrôle et par passage.
-- Les contrôles relisent articles_propres et rejets après chaque nettoyage et vérifient
-- des faits (aucune date future, aucun titre vide…) : src/gazette/clean/checks.py.
CREATE TABLE controles (
    jour_collecte  date        NOT NULL,
    verifie_le     timestamptz NOT NULL,
    controle       text        NOT NULL,
    gravite        text        NOT NULL CHECK (gravite IN ('critique', 'avertissement')),
    ok             boolean     NOT NULL,
    valeur         text,
    seuil          text,
    detail         text,
    PRIMARY KEY (jour_collecte, verifie_le, controle)
);

CREATE INDEX controles_echecs_idx ON controles (jour_collecte) WHERE NOT ok;

COMMENT ON TABLE controles IS 'Historique des contrôles de qualité, un par contrôle et par passage';
COMMENT ON COLUMN controles.gravite IS 'critique : donnée fausse, la tâche échoue ; avertissement : dérive à surveiller';
COMMENT ON COLUMN controles.valeur IS 'Valeur mesurée (JSON), à comparer au seuil';
