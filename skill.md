# PeerDB SQL Reference

**Documentation**: [PeerDB Docs](https://docs.peerdb.io/sql/reference) | [Creating Mirrors](https://docs.peerdb.io/sql/commands/create-mirror)

## Important: Auto-Sync Behavior

**New tables are NOT automatically mirrored.** When you create a new table in PostgreSQL, you must manually create a mirror using `CREATE MIRROR` for it to sync to ClickHouse.

## Overview

PeerDB provides SQL commands for ETL/ELT operations between PostgreSQL and data warehouses. The main components are:

- **Peers**: Source/destination connections (PostgreSQL, ClickHouse, Snowflake, BigQuery, etc.)
- **Mirrors**: Sync jobs that replicate data from source to target

---

## CREATE PEER

Create a connection to a data source.

### PostgreSQL Peer
```sql
CREATE PEER <peer_name> FROM POSTGRES WITH
(
    host = 'hostname',
    port = 5432,
    user = 'username',
    password = 'password',
    database = 'database_name'
);
```

### ClickHouse Peer
```sql
CREATE PEER <peer_name> FROM CLICKHOUSE WITH
(
    host = 'hostname',
    port = 9000,
    user = 'username',
    password = 'password',
    database = 'database_name',
    disable_tls = true
);
```

---

## CREATE MIRROR

### CDC Mirror (Real-time Change Data Capture)

Streams change feed in real-time from source to target using PostgreSQL logical replication.

```sql
CREATE MIRROR [IF NOT EXISTS] <mirror_name>
FROM <source_peer> TO <target_peer>
WITH TABLE MAPPING
(
  <schema_name>.<source_table_name>:<target_table_name>,
  <schema_name>.<source_table_name>:<target_table_name>,
  -- JSON syntax for column exclusion
  {
    from: <schema_name>.<source_table_name>,
    to: <target_table_name>,
    exclude: [column1, column2, column3...]
  }
)
WITH (
  do_initial_copy = <true|false>,           -- Required: Include initial snapshot
  max_batch_size = <max_batch_size>,        -- Max rows per batch
  sync_interval = <sync_interval>,          -- Seconds between syncs
  publication_name = '<publication_name>',  -- Optional: Custom publication
  replication_slot_name = '<slot_name>',    -- Optional: Custom replication slot
  snapshot_num_rows_per_partition = <N>,   -- Rows per partition in snapshot
  snapshot_max_parallel_workers = <N>,     -- Threads per table in snapshot
  snapshot_num_tables_in_parallel = <N>,   -- Tables to snapshot in parallel
  soft_delete = <true|false>,               -- Use soft deletes instead of DELETE
  synced_at_col_name = '<column_name>',     -- Default: _PEERDB_SYNCED_AT
  soft_delete_col_name = '<column_name>'    -- Default: _PEERDB_IS_DELETED
);
```

#### Example: Mirror PostgreSQL to ClickHouse
```sql
-- Create mirror for single table
CREATE MIRROR api_keys_mirror FROM postgres_main TO clickhouse_analytics
WITH TABLE MAPPING (public.api_keys:api_keys)
WITH (do_initial_copy = true);

-- Create mirror for multiple tables
CREATE MIRROR app_tables_mirror FROM postgres_main TO clickhouse_analytics
WITH TABLE MAPPING
(
  public.api_keys:api_keys,
  public.api_plan_limits:api_plan_limits
)
WITH (do_initial_copy = true);

-- Mirror with column exclusion
CREATE MIRROR users_mirror FROM postgres_main TO clickhouse_analytics
WITH TABLE MAPPING
({
  from: public.users,
  to: users,
  exclude: [password, ssn, credit_card]
})
WITH (do_initial_copy = true);
```

### Streaming Query Mirror

Periodically streams query results from source to target.

```sql
CREATE MIRROR <mirror_name> [IF NOT EXISTS] FROM
  <source_peer> TO <target_peer> FOR
$$
  SELECT * FROM <source_table_name> WHERE
  <watermark_column> BETWEEN {{.start}} AND {{.end}}
$$
WITH (
  destination_table_name = '<schema_qualified_destination_table>',
  watermark_column = '<watermark_column>',
  watermark_table_name = '<watermark_table>',
  mode = '<mode>',                          -- 'append' or 'upsert'
  unique_key_columns = '<column1,column2>', -- Required for upsert mode
  parallelism = <N>,                        -- Number of threads
  refresh_interval = <N>,                   -- Seconds between syncs (default: 10)
  num_rows_per_partition = 100000,
  initial_copy_only = <true|false>,
  setup_watermark_table_on_destination = <true|false>
);
```

#### XMIN Query Replication
```sql
-- Use xmin system column as watermark (DELETEs not supported)
CREATE MIRROR xmin_mirror FROM pg_peer TO ch_peer FOR
$$ SELECT * FROM users $$
WITH (
  destination_table_name = 'public.users',
  watermark_column = 'xmin',
  watermark_table_name = 'public.users',
  mode = 'append',
  parallelism = 10,
  refresh_interval = 30,
  num_rows_per_partition = 100
);
```

---

## DROP MIRROR

```sql
DROP MIRROR <mirror_name>;
```

---

## PAUSE/RESUME MIRROR

```sql
PAUSE MIRROR <mirror_name>;
RESUME MIRROR <mirror_name>;
```

---

## RESYNC MIRROR

```sql
RESYNC MIRROR <mirror_name>;
```

---

## DROP PEER

```sql
DROP PEER <peer_name>;
```

---

## Key Parameters Explained

| Parameter | Description |
|-----------|-------------|
| `do_initial_copy` | Include existing data in initial sync |
| `max_batch_size` | Maximum rows per batch for CDC sync |
| `sync_interval` | Seconds between CDC syncs |
| `soft_delete` | Mark rows as deleted instead of deleting |
| `snapshot_num_tables_in_parallel` | Tables snapshot concurrently |
| `snapshot_max_parallel_workers` | Threads per table during snapshot |
| `mode` | `append` (insert only) or `upsert` (update if exists) |
| `watermark_column` | Timestamp/int column for tracking progress |
| `parallelism` | Number of worker threads |

---

## Current Setup

### Peers Created
- **postgres_main**: PostgreSQL source (port 5432)
- **clickhouse_analytics**: ClickHouse target (default database, port 9000)

### Mirrors Created
- **api_keys_mirror**: `public.api_keys` → `api_keys`
- **api_plan_limits_mirror**: `public.api_plan_limits` → `api_plan_limits`

### Connecting to PeerDB CLI
```bash
PGPASSWORD=peerdb psql -h localhost -p 9900 -U postgres
```

---

## Adding New Tables

When you add a new table to PostgreSQL:

1. Create a mirror for it:
```sql
CREATE MIRROR new_table_mirror FROM postgres_main TO clickhouse_analytics
WITH TABLE MAPPING (public.new_table:new_table)
WITH (do_initial_copy = true);
```

2. Or add it to an existing mirror by dropping and recreating with multiple table mappings.

---

## Sources

- [PeerDB SQL Reference](https://docs.peerdb.io/sql/reference)
- [Creating Mirrors](https://docs.peerdb.io/sql/commands/create-mirror)
- [PeerDB GitHub](https://github.com/PeerDB-io/peerdb)
