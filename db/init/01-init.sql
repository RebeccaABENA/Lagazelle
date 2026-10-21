-- Exécuté une seule fois, au premier démarrage du volume PostgreSQL.
-- La base `airflow` (métadonnées) est créée par POSTGRES_DB.
-- Ici : la base métier `gazette`, avec l'extension pgvector. Aucune table pour l'instant.
CREATE DATABASE gazette;

\connect gazette
CREATE EXTENSION IF NOT EXISTS vector;
