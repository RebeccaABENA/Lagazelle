-- Exécuté une seule fois, au premier démarrage du volume PostgreSQL.
-- La base `airflow` (métadonnées) est créée par POSTGRES_DB.
-- Ici : la base métier `gazette`, avec l'extension pgvector. Les tables (sources,
-- articles_vus, articles_propres…) sont créées par les migrations : python -m gazette db migrate.
CREATE DATABASE gazette;

\connect gazette
CREATE EXTENSION IF NOT EXISTS vector;
