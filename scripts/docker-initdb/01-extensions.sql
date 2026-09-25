-- Runs once, when the Docker volume is first initialised (see docker-compose.yml).
-- The official postgres image has already created POSTGRES_USER and POSTGRES_DB;
-- this adds the extensions fernKam's migrations need. pgvector in particular is
-- not a "trusted" extension, so it must be created by a superuser up front.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
