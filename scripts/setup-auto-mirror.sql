-- Auto-mirror setup for EchoDB
-- Run this on your PostgreSQL database to enable automatic mirror creation
--
-- Environment Variables:
--   SYNC_SCHEMA: Schema to watch for new tables (default: public)

-- Enable pgcrypto for UUID generation (if not already enabled)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Function to notify when a new table is created
CREATE OR REPLACE FUNCTION notify_new_table()
RETURNS event_trigger AS $$
DECLARE
  obj record;
  table_name text;
  schema_name text;
BEGIN
  -- Loop through all objects in this DDL command
  FOR obj IN SELECT * FROM pg_event_trigger_ddl_commands() WHERE command_tag = 'CREATE TABLE'
  LOOP
    -- Extract schema and table name
    schema_name := obj.object_identity;
    -- Skip if not in the target schema
    IF schema_name LIKE '${SYNC_SCHEMA:-public}.' THEN
      -- Send notification with table name
      PERFORM pg_notify('peerdb_create_mirror',
        json_build_object(
          'schema', '${SYNC_SCHEMA:-public}',
          'table', substring(obj.object_identity from position('.' in obj.object_identity) + 1),
          'timestamp', now()
        )::text
      );
    END IF;
  END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Create event trigger for CREATE TABLE commands
DROP EVENT TRIGGER IF EXISTS peerdb_auto_mirror_trigger;
CREATE EVENT TRIGGER peerdb_auto_mirror_trigger
ON ddl_command_end
WHEN tag IN ('CREATE TABLE')
EXECUTE FUNCTION notify_new_table();

-- Log successful setup
DO $$
BEGIN
  RAISE NOTICE 'EchoDB auto-mirror trigger installed. Tables created in ${SYNC_SCHEMA:-public} schema will trigger notifications.';
END $$;
