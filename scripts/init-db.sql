-- Manual alternative to `uv run fernkam setup-db` for people who prefer psql.
-- Run once as a PostgreSQL superuser, from the repo root:
--
--   psql -U postgres -v pw='choose-a-password' -f scripts/init-db.sql
--
-- then put the same password in backend/.env:
--   PG_URL=postgresql+asyncpg://fernkam_user:choose-a-password@localhost:5432/fernkam
--
-- Requires the pgvector extension to be installed on the server (see README).

\if :{?pw}
\else
  \echo 'Pass a password: psql -U postgres -v pw=''...'' -f scripts/init-db.sql'
  \quit
\endif

SELECT format('CREATE ROLE fernkam_user LOGIN PASSWORD %L', :'pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fernkam_user') \gexec

SELECT 'CREATE DATABASE fernkam OWNER fernkam_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'fernkam') \gexec

\connect fernkam

-- Extensions are created here, as superuser: pgvector is not a "trusted"
-- extension, so the migrations (which run as fernkam_user) cannot create it.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

GRANT ALL ON SCHEMA public TO fernkam_user;

\echo 'Done. Set PG_URL in backend/.env, then start fernKam (it migrates on startup).'
