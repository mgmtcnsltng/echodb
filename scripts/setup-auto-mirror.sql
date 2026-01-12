-- Auto-mirror setup for EchoDB
-- Run this on your PostgreSQL database to enable automatic mirror creation/deletion
--
-- Environment Variables:
--   SYNC_SCHEMA: Schema(s) to watch for new tables (comma-separated, default: public)
--                Examples: "public", "public,analytics", "public,analytics,logs"

-- Enable pgcrypto for UUID generation (if not already enabled)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Function to check if a schema is in the watch list
CREATE OR REPLACE FUNCTION is_schema_in_watch_list(schema_name text, watch_list text) RETURNS boolean AS $$
DECLARE
  item text;
BEGIN
  -- Handle empty or NULL watch list (default to public)
  IF watch_list IS NULL OR watch_list = '' THEN
    RETURN schema_name = 'public';
  END IF;

  -- Check each schema in the comma-separated list
  FOREACH item IN ARRAY regexp_split_to_array(watch_list, ',')
  LOOP
    IF schema_name = trim(item) THEN
      RETURN true;
    END IF;
  END LOOP;

  RETURN false;
END;
$$ LANGUAGE plpgsql;

-- Function to notify when a new table is created
CREATE OR REPLACE FUNCTION notify_new_table()
RETURNS event_trigger AS $$
DECLARE
  obj record;
  schema_name text;
  table_name text;
  watch_list text := '${SYNC_SCHEMA:-public}';
BEGIN
  -- Loop through all objects in this DDL command
  FOR obj IN SELECT * FROM pg_event_trigger_ddl_commands() WHERE command_tag = 'CREATE TABLE'
  LOOP
    -- Extract schema.table name from object_identity
    -- object_identity format is typically "schema.table_name"
    schema_name := split_part(obj.object_identity, '.', 1);

    -- Skip if not in the target schemas
    IF NOT is_schema_in_watch_list(schema_name, watch_list) THEN
      CONTINUE;
    END IF;

    -- Extract table name (after the first dot)
    table_name := substring(obj.object_identity from position('.' in obj.object_identity) + 1);

    -- Send notification with schema and table name
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
  watch_list text := '${SYNC_SCHEMA:-public}';
BEGIN
  -- Loop through all objects in this DDL command
  FOR obj IN SELECT * FROM pg_event_trigger_ddl_commands() WHERE command_tag = 'DROP TABLE'
  LOOP
    -- Extract schema.table name from object_identity
    schema_name := split_part(obj.object_identity, '.', 1);

    -- Skip if not in the target schemas
    IF NOT is_schema_in_watch_list(schema_name, watch_list) THEN
      CONTINUE;
    END IF;

    -- Extract table name (after the first dot)
    table_name := substring(obj.object_identity from position('.' in obj.object_identity) + 1);

    -- Send notification with schema and table name
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
DECLARE
  watch_list text := '${SYNC_SCHEMA:-public}';
BEGIN
  RAISE NOTICE 'EchoDB auto-mirror triggers installed.';
  RAISE NOTICE 'Watching schemas: %', watch_list;
  RAISE NOTICE 'Tables created/dropped in these schemas will trigger notifications.';
END $$;
