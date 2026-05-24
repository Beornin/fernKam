-- Run once as a PostgreSQL superuser:
--   psql -U postgres -f scripts/init-db.sql
--
-- You will be prompted for a password via \password after creation.
-- Or pass it inline: replace 'changeme' below.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'fernkam_user') THEN
    CREATE USER fernkam_user WITH PASSWORD 'changeme';
    RAISE NOTICE 'Created user fernkam_user. Run: ALTER USER fernkam_user PASSWORD ''<your-password>'' to change it.';
  ELSE
    RAISE NOTICE 'User fernkam_user already exists.';
  END IF;
END
$$;

SELECT 'CREATE DATABASE fernkam OWNER fernkam_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'fernkam') \gexec

\connect fernkam

-- Extensions (must be run as superuser)
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- These require separate installation on Windows (see SETUP.md):
-- CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector
-- CREATE EXTENSION IF NOT EXISTS postgis;   -- PostGIS

GRANT ALL ON SCHEMA public TO fernkam_user;
GRANT ALL PRIVILEGES ON DATABASE fernkam TO fernkam_user;

\echo 'Done. Update .env with your actual password before running alembic.'
