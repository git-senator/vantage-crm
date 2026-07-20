-- Role separation for row-level security.
--
-- Runs once, at first container init, via docker-entrypoint-initdb.d.
--
-- Why two roles:
--   PostgreSQL table OWNERS bypass row-level security. If the application role
--   owned the tables, every RLS policy would be silently inert while looking
--   correct in the schema. So migrations run as an owner role, and the app
--   connects as a non-owner with no BYPASSRLS.
--
-- Production note: these development passwords are replaced by secrets from the
-- deployment environment. See docs/SECURITY.md §6.

-- ---------------------------------------------------------------- migrator
-- Owns the schema. Used only by Alembic.
CREATE ROLE vantage_migrator WITH LOGIN PASSWORD 'dev_migrator_password';
ALTER ROLE vantage_migrator SET search_path = public;

-- ------------------------------------------------------------- application
-- Runtime role. Explicitly NOT a superuser, NOT the owner, no BYPASSRLS.
CREATE ROLE vantage_app WITH LOGIN PASSWORD 'dev_app_password' NOBYPASSRLS;
ALTER ROLE vantage_app SET search_path = public;

GRANT CONNECT ON DATABASE vantage TO vantage_migrator, vantage_app;
GRANT USAGE ON SCHEMA public TO vantage_app;
GRANT CREATE, USAGE ON SCHEMA public TO vantage_migrator;

-- CREATE on the DATABASE (not just the schema) is required to install
-- extensions. pgcrypto, citext and pg_trgm are "trusted" extensions in
-- PostgreSQL 13+, so this grant is sufficient and no superuser is needed at
-- migration time. Granting on the schema alone is not enough — the baseline
-- migration fails with "permission denied to create extension".
GRANT CREATE ON DATABASE vantage TO vantage_migrator;

-- Default privileges: tables created later by the migrator automatically grant
-- DML to the app role, so every migration does not need a GRANT block.
ALTER DEFAULT PRIVILEGES FOR ROLE vantage_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vantage_app;

ALTER DEFAULT PRIVILEGES FOR ROLE vantage_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO vantage_app;

-- NOTE for Phase 1: audit_logs must be append-only, which means overriding the
-- default above with an explicit revoke in the migration that creates it:
--
--   REVOKE UPDATE, DELETE ON audit_logs FROM vantage_app;
--
-- Enforcing immutability at the grant level means an application bug cannot
-- rewrite history. See docs/DATABASE.md §5.
