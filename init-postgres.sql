-- PostgreSQL Initialization Script for EchoDB
-- This script sets up the initial schema for your application database
--
-- Environment Variables:
--   POSTGRES_DB: Database name (default: postgres)

-- ============================================================================
-- CLEANUP: Remove unwanted default databases
-- ============================================================================

-- Drop the default 'postgres' database if it exists (it's created by PostgreSQL by default)
-- Note: We need to connect to 'echodb' database first, then drop 'postgres'
DO $$
DECLARE
    result TEXT;
BEGIN
    -- Check if postgres database exists and allows connections
    IF EXISTS (SELECT 1 FROM pg_database WHERE datname = 'postgres' AND datallowconn) THEN
        -- Terminate all connections to the postgres database first
        EXECUTE 'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = ''postgres'' AND pid <> pg_backend_pid()';

        -- Drop the database
        EXECUTE 'DROP DATABASE postgres';
        RAISE NOTICE 'Dropped default postgres database';
    END IF;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Could not drop postgres database: %', SQLERRM;
END $$;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- DEMONSTRATION TABLE
-- This table demonstrates real-time CDC replication to ClickHouse via PeerDB
-- ============================================================================

-- Users Table - Demonstrates basic user management with profile data
CREATE TABLE IF NOT EXISTS users
(
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT UNIQUE NOT NULL,
    username TEXT UNIQUE NOT NULL,
    first_name TEXT,
    last_name TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create index for better query performance
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ============================================================================
-- AUTO-MIRROR EVENT TRIGGERS
-- These triggers enable automatic mirror creation for new tables
-- ============================================================================

-- Helper function to check if schema is in watch list
CREATE OR REPLACE FUNCTION is_schema_in_watch_list(schema_name text, watch_list text)
RETURNS BOOLEAN AS $$
DECLARE
  item text;
BEGIN
  FOREACH item IN ARRAY regexp_split_to_array(watch_list, '\s*,\s*')
  LOOP
    IF item = schema_name THEN
      RETURN TRUE;
    END IF;
  END LOOP;
  RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

-- Function to notify when a new table is created
CREATE OR REPLACE FUNCTION notify_new_table()
RETURNS event_trigger AS $$
DECLARE
  obj record;
  schema_name text;
  table_name text;
  watch_list text := 'public';
BEGIN
  FOR obj IN SELECT * FROM pg_event_trigger_ddl_commands() WHERE command_tag = 'CREATE TABLE'
  LOOP
    schema_name := split_part(obj.object_identity, '.', 1);

    IF NOT is_schema_in_watch_list(schema_name, watch_list) THEN
      CONTINUE;
    END IF;

    table_name := substring(obj.object_identity from position('.' in obj.object_identity) + 1);

    PERFORM pg_notify('peerdb_create_mirror',
      json_build_object(
        'schema', schema_name,
        'table', table_name,
        'timestamp', now()
      )::text
    );
  END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Function to notify when a table is dropped
CREATE OR REPLACE FUNCTION notify_drop_table()
RETURNS event_trigger AS $$
DECLARE
  obj record;
  schema_name text;
  table_name text;
  watch_list text := 'public';
BEGIN
  FOR obj IN SELECT * FROM pg_event_trigger_ddl_commands() WHERE command_tag = 'DROP TABLE'
  LOOP
    schema_name := split_part(obj.object_identity, '.', 1);

    IF NOT is_schema_in_watch_list(schema_name, watch_list) THEN
      CONTINUE;
    END IF;

    table_name := substring(obj.object_identity from position('.' in obj.object_identity) + 1);

    PERFORM pg_notify('peerdb_drop_mirror',
      json_build_object(
        'schema', schema_name,
        'table', table_name,
        'timestamp', now()
      )::text
    );
  END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Create event trigger for CREATE TABLE commands
DROP EVENT TRIGGER IF EXISTS peerdb_auto_mirror_trigger;
CREATE EVENT TRIGGER peerdb_auto_mirror_trigger
ON ddl_command_end
WHEN tag IN ('CREATE TABLE')
EXECUTE FUNCTION notify_new_table();

-- Create event trigger for DROP TABLE commands
DROP EVENT TRIGGER IF EXISTS peerdb_auto_mirror_drop_trigger;
CREATE EVENT TRIGGER peerdb_auto_mirror_drop_trigger
ON ddl_command_end
WHEN tag IN ('DROP TABLE')
EXECUTE FUNCTION notify_drop_table();

-- Log successful setup
DO $$
BEGIN
  RAISE NOTICE 'EchoDB initialization complete.';
  RAISE NOTICE '  - Created users table';
  RAISE NOTICE '  - Installed auto-mirror event triggers';
  RAISE NOTICE '  - Watching schemas: public';
END $$;
