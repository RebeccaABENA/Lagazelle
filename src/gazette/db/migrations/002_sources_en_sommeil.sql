-- 002 : vue des sources en sommeil (3 échecs consécutifs ou plus, cf. SLEEP_THRESHOLD
-- dans src/gazette/sources.py). Réveil : python -m gazette sources wake <slug>.
CREATE VIEW sources_en_sommeil AS
SELECT nom, type, url_flux, echecs_consecutifs, derniere_reussite
FROM sources
WHERE actif AND echecs_consecutifs >= 3;
