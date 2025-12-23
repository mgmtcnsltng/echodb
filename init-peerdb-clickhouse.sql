-- PeerDB ClickHouse Setup for EchoDB
-- This script sets up permissions for syncing tables into ClickHouse
--
-- Environment Variables:
--   CLICKHOUSE_DATABASE: Database name (default: default)
--   CLICKHOUSE_USER: ClickHouse user (default: default)
--
-- Note: Synced tables will have the same name as PostgreSQL tables
-- Example: postgres.public.api_keys -> clickhouse.${CLICKHOUSE_DATABASE}.api_keys

-- Grant necessary permissions on the database for the user
GRANT INSERT, SELECT, DROP, CREATE TABLE ON ${CLICKHOUSE_DATABASE:-default}.* TO ${CLICKHOUSE_USER:-default};
GRANT CREATE TEMPORARY TABLE, s3 ON *.* TO ${CLICKHOUSE_USER:-default};
GRANT ALTER ADD COLUMN ON ${CLICKHOUSE_DATABASE:-default}.* TO ${CLICKHOUSE_USER:-default};
GRANT ALTER MODIFY COLUMN ON ${CLICKHOUSE_DATABASE:-default}.* TO ${CLICKHOUSE_USER:-default};
GRANT ALTER DROP COLUMN ON ${CLICKHOUSE_DATABASE:-default}.* TO ${CLICKHOUSE_USER:-default};
