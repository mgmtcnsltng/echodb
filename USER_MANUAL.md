# EchoDB User Manual

Complete guide for deploying and using EchoDB - a real-time data replication system from PostgreSQL to ClickHouse using PeerDB with production-ready auto-mirror, high availability, and data consistency verification.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Deployment Scenarios](#deployment-scenarios)
3. [Quick Start](#quick-start)
4. [Auto-Mirror Feature](#auto-mirror-feature)
5. [High Availability](#high-availability)
6. [Data Consistency](#data-consistency)
7. [Configuration](#configuration)
8. [Monitoring](#monitoring)
9. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EchoDB Architecture                               │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────┐         ┌──────────────┐         ┌──────────────┐
  │  PostgreSQL  │         │    PeerDB    │         │  ClickHouse  │
  │  (App DB)    │────────▶│   (ETL/ELT)  │────────▶│  (Analytics) │
  │              │   CDC   │              │  Sync   │              │
  └──────┬───────┘         └──────┬───────┘         └──────────────┘
         │                       │
         │ pg_notify()            │
         ▼                       ▼
  ┌──────────────┐         ┌──────────────┐
  │ Event Trigger│         │    MinIO     │
  │  (DDL Hook)  │         │  (Staging)   │
  └──────────────┘         └──────────────┘
         │
         ▼
  ┌─────────────────────────────────────────────────────────────┐
  │           Auto-Mirror Workers (HA with Leader Election)     │
  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
  │  │  Worker-1    │  │  Worker-2    │  │  Consistency │      │
  │  │  (Leader)    │  │  (Follower)  │  │   Checker    │      │
  │  └──────────────┘  └──────────────┘  └──────────────┘      │
  │                                                              │
  │  Features:                                                   │
  │  • Leader election (Redis)                                  │
  │  • Circuit breakers                                         │
  │  • Retry logic with exponential backoff                     │
  │  • Auto-reconnect                                           │
  │  • Duplicate prevention                                     │
  │  • Health check endpoints                                   │
  │  • Data consistency verification                            │
  └─────────────────────────────────────────────────────────────┘
```

### Components

#### Data Layer
| Component | Purpose | Ports |
|-----------|---------|-------|
| **PostgreSQL** | Primary application database with logical replication | 5432 |
| **ClickHouse** | Analytical database for fast aggregations | 8123 (HTTP), 9000 (Native) |
| **Redis** | Distributed locks, caching, and coordination | 6379 |

#### ETL/ELT Layer
| Component | Purpose | Ports |
|-----------|---------|-------|
| **PeerDB Server** | SQL interface for managing data mirrors | 9900 |
| **PeerDB Flow API** | gRPC/HTTP API for mirror operations | 8112 (gRPC), 8113 (HTTP) |
| **PeerDB Flow Worker** | Background worker for CDC sync | - |
| **PeerDB UI** | Web dashboard for mirror management | 3000 |
| **MinIO** | S3-compatible storage for CDC staging | 9001 (API), 9002 (Console) |

#### Auto-Mirror Layer
| Component | Purpose | Ports |
|-----------|---------|-------|
| **Auto-Mirror Worker 1** | Primary worker with leader election | 8080 (health) |
| **Auto-Mirror Worker 2** | Secondary worker for HA | 8081 (health) |
| **Consistency Checker** | Data verification service | 8090 (HTTP API) |

---

## Deployment Scenarios

EchoDB supports three deployment scenarios:

### Scenario 1: Local Deployment

**Use Case**: Development and testing on your local machine

**Configuration**: Default `.env` file works as-is

```bash
# 1. Copy environment file
cp .env.example .env

# 2. Start all services
docker compose up -d

# 3. Verify deployment
docker compose ps
```

**What Happens**:
- All services (PostgreSQL, ClickHouse, PeerDB, etc.) run in Docker containers
- Auto-setup runs automatically on first startup
- Workers connect to local databases via Docker network
- Everything works out of the box with zero manual configuration

**Verification**:
```bash
# Check all services are healthy
curl http://localhost:8080/health  # Worker-1
curl http://localhost:8081/health  # Worker-2
curl http://localhost:8090/health  # Consistency Checker

# Create a test table
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  CREATE TABLE test_local (id SERIAL, name TEXT);
"

# Verify mirror was created
docker exec echodb-peerdb-1 psql -h peerdb -U echodb -d echodb -c "
  SELECT name FROM peerdb.mirrors WHERE name LIKE '%test_local%';
"
```

---

### Scenario 2: Remote VM Deployment

**Use Case**: Deploying EchoDB on a remote server (VM, cloud instance)

**Configuration**: Default `.env` file works as-is

```bash
# 1. Copy environment file
cp .env.example .env

# 2. (Optional) Change exposed ports if needed
# Edit docker-compose.yml to bind ports to specific interfaces
# Example: "127.0.0.1:8080:8080" for localhost only

# 3. Start all services
docker compose up -d

# 4. Configure firewall/reverse proxy as needed
```

**What Happens**:
- All services run on the remote VM
- Services are accessible via VM's IP address or domain name
- Auto-setup runs automatically
- Workers connect to databases via Docker network (internal)

**Accessing Services Remotely**:
```bash
# From your local machine, replace YOUR_VM_IP with actual IP

# Access PeerDB UI
http://YOUR_VM_IP:3000

# Access Temporal UI
http://YOUR_VM_IP:8085

# Check worker health
curl http://YOUR_VM_IP:8080/health
curl http://YOUR_VM_IP:8081/health
curl http://YOUR_VM_IP:8090/health

# Create table remotely
ssh user@YOUR_VM_IP
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  CREATE TABLE test_remote (id SERIAL, name TEXT);
"
```

**Security Recommendations**:
- Use reverse proxy (nginx/traefik) for web UIs
- Restrict database port access (5432, 8123, 9000) via firewall
- Use VPN or SSH tunnel for database access
- Change all default passwords in `.env`
- Use HTTPS/TLS for external connections

---

### Scenario 3: Remote Database Connections

**Use Case**: EchoDB runs locally/on VM, but connects to remote PostgreSQL and/or ClickHouse

**Configuration**: Update `.env` with remote database connection details

#### 3A. Remote PostgreSQL Only

**Use Case**: PostgreSQL is on a remote server, ClickHouse is local

```bash
# Update .env file
POSTGRES_HOST=your-postgres.example.com
POSTGRES_PORT=5432
POSTGRES_USER=your_remote_user
POSTGRES_PASSWORD=your_remote_password
POSTGRES_DB=your_remote_db

# ClickHouse remains local
CLICKHOUSE_HOST=clickhouse
CLICKHOUSE_PORT=8123
```

**What Happens**:
- Auto-setup connects to remote PostgreSQL to install triggers
- Workers receive notifications from remote PostgreSQL
- Mirrors replicate data from remote PostgreSQL to local ClickHouse
- No changes to PeerDB/ClickHouse setup needed

**Important Notes**:
- Remote PostgreSQL must allow connections from your EchoDB host
- Configure `pg_hba.conf` on remote PostgreSQL to allow your IP
- Ensure firewall allows port 5432
- Use SSL/TLS for production deployments

**Verification**:
```bash
# Test PostgreSQL connection
docker exec echodb-auto-setup-1 psql -h your-postgres.example.com -U your_remote_user -d your_remote_db -c "SELECT 1;"

# Create table on remote PostgreSQL
psql -h your-postgres.example.com -U your_remote_user -d your_remote_db -c "
  CREATE TABLE test_remote_pg (id SERIAL, name TEXT);
"

# Verify mirror created locally
curl http://localhost:8080/metrics
```

---

#### 3B. Remote ClickHouse Only

**Use Case**: ClickHouse is on a remote server, PostgreSQL is local

```bash
# PostgreSQL remains local
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# Update .env for remote ClickHouse
CLICKHOUSE_HOST=your-clickhouse.example.com
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=your_remote_user
CLICKHOUSE_PASSWORD=your_remote_password
CLICKHOUSE_DATABASE=your_remote_db
```

**What Happens**:
- Workers create mirrors targeting remote ClickHouse
- Data is replicated from local PostgreSQL to remote ClickHouse
- Consistency checker verifies data on remote ClickHouse

**Important Notes**:
- Remote ClickHouse must allow connections from your EchoDB host
- Configure ClickHouse `users.xml` and network rules
- Ensure firewall allows port 8123 (or 9000 for native)
- Consider using HTTPS for port 8123

**Verification**:
```bash
# Create table locally
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  CREATE TABLE test_local_pg (id SERIAL, name TEXT);
"

# Query remote ClickHouse directly
clickhouse-client --host your-clickhouse.example.com --user your_remote_user --password your_remote_password --query "
  SELECT * FROM your_remote_db.test_local_pg;
"
```

---

#### 3C. Both Remote PostgreSQL and ClickHouse

**Use Case**: Both databases are on remote servers

```bash
# Remote PostgreSQL
POSTGRES_HOST=postgres1.example.com
POSTGRES_PORT=5432
POSTGRES_USER=remote_pg_user
POSTGRES_PASSWORD=remote_pg_password
POSTGRES_DB=production_db

# Remote ClickHouse
CLICKHOUSE_HOST=clickhouse.example.com
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=remote_ch_user
CLICKHOUSE_PASSWORD=remote_ch_password
CLICKHOUSE_DATABASE=analytics_db
```

**What Happens**:
- EchoDB acts as a CDC bridge between two remote databases
- Auto-setup installs triggers on remote PostgreSQL
- Workers connect to both remote databases
- Data flows from remote PostgreSQL to remote ClickHouse

**Network Requirements**:
- EchoDB host must have network access to both remote databases
- Both firewalls must allow connections from EchoDB host IP
- Consider using VPN for secure connections
- Monitor network latency between all three systems

**Architecture**:
```
┌─────────────────┐         ┌─────────────────┐
│ Remote          │         │ Remote          │
│ PostgreSQL      │────────▶│ ClickHouse      │
│ (Source)        │   CDC   │ (Target)        │
└─────────────────┘         └─────────────────┘
         ▲                           ▲
         │                           │
         └───────────┬───────────────┘
                     │
              ┌──────┴──────┐
              │   EchoDB    │
              │  (CDC Hub)  │
              └─────────────┘
```

**Verification**:
```bash
# Check worker connections
curl http://localhost:8080/ready

# Create table on remote PostgreSQL
psql -h postgres1.example.com -U remote_pg_user -d production_db -c "
  CREATE TABLE test_both_remote (id SERIAL, data TEXT);
"

# Verify in remote ClickHouse
clickhouse-client --host clickhouse.example.com --query "
  SELECT count() FROM analytics_db.test_both_remote;
"
```

---

## Quick Start

### Local Deployment (Fastest)

```bash
# 1. Clone and configure
git clone <repo-url>
cd echodb
cp .env.example .env

# 2. Start all services (zero manual setup required!)
docker compose up -d

# 3. Wait for services to be healthy (auto-setup runs automatically)
docker compose ps

# 4. Create a test table
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT
  );
"

# 5. Verify mirror created automatically
curl http://localhost:8080/metrics

# 6. Query ClickHouse for replicated data
docker exec echodb-clickhouse-1 clickhouse-client --query "
  SELECT * FROM postgres.users LIMIT 10;
"
```

### Remote Database Connection

```bash
# 1. Edit .env with remote database details
vim .env

# 2. Example: Remote PostgreSQL
POSTGRES_HOST=your-postgres.example.com
POSTGRES_PORT=5432
POSTGRES_USER=your_user
POSTGRES_PASSWORD=your_password
POSTGRES_DB=your_database

# 3. Start services
docker compose up -d

# 4. Verify connection
docker logs echodb-auto-setup-1
```

---

## Auto-Mirror Feature

The auto-mirror feature automatically creates PeerDB mirrors whenever a new table is created in PostgreSQL.

### How It Works

```
1. USER creates table in PostgreSQL
   │
   │  CREATE TABLE users (id SERIAL, name TEXT);
   ▼
2. POSTGRESQL Event Trigger fires
   │
   │  Trigger "peerdb_auto_mirror_trigger" detects CREATE TABLE
   ▼
3. NOTIFICATION sent via pg_notify()
   │
   │  Channel: 'peerdb_create_mirror'
   │  Payload: {"schema": "public", "table": "users"}
   ▼
4. LEADER WORKER receives notification
   │
   │  Worker-1 (leader) acquires notification lock via Redis
   │  Worker-2 (follower) skips processing (duplicate prevention)
   ▼
5. CIRCUIT BREAKER checks PeerDB availability
   │
   │  If PeerDB is healthy: Proceed
   │  If circuit is open: Skip and retry later
   ▼
6. WORKER creates PeerDB mirror with retry logic
   │
   │  CREATE MIRROR users_mirror
   │  FROM postgres_main
   │  TO clickhouse_analytics
   │  WITH TABLE MAPPING (public.users:users)
   ▼
7. CONSISTENCY CHECKER verifies data
   │
   │  After 5 seconds: Check row counts match
   │  If mismatch: Retry up to 3 times
   ▼
8. DATA synced to ClickHouse
   │
   │  Table 'users' now available in ClickHouse
```

### Multi-Schema Support

Watch multiple PostgreSQL schemas:

```bash
# .env configuration
SYNC_SCHEMA=public,analytics,logs,inventory
```

Workers will only auto-mirror tables in the specified schemas.

### Excluding Tables

Exclude specific tables from auto-mirror:

```bash
# .env configuration
EXCLUDED_TABLES=temp_*,_tmp_*,spatial_ref_sys,geometry_columns
```

### DROP TABLE Support

Dropped tables are automatically handled:

```sql
DROP TABLE users;
-- Notification sent: peerdb_drop_mirror
-- Worker automatically drops mirror: DROP MIRROR users_mirror
```

---

## High Availability

### Leader Election

EchoDB runs two worker instances with Redis-based leader election:

```
┌─────────────┐     ┌─────────────┐
│  Worker-1   │     │  Worker-2   │
│ (Candidate) │────▶│ (Candidate) │
└──────┬──────┘     └──────┬──────┘
       │                   │
       │    Acquire        │
       └─────────┬─────────┘
                 ▼
         ┌───────────────┐
         │     Redis     │
         │ Distributed   │
         │     Lock      │
         └───────────────┘
                 │
        Only one wins
                 │
        ┌────────▼────────┐
        │   Worker-1      │
        │   (Leader)      │
        │  Processes all  │
        │  notifications  │
        └─────────────────┘
```

**Configuration**:
```bash
# Leader election settings
LEADER_ELECTION_TTL=30        # Leadership lease (seconds)
LEADER_ELECTION_INTERVAL=10   # Retry interval when follower
REDIS_HOST=redis
REDIS_PORT=6379
```

**Failover Behavior**:
- If leader fails: Follower acquires leadership within 10-30 seconds
- Zero data loss: Notifications are queued in PostgreSQL
- Duplicate prevention: Redis-based notification tracking

### Circuit Breaker Pattern

Prevents cascading failures when services are unavailable:

```
State Machine:
CLOSED ──(failures)──▶ OPEN ──(timeout)──▶ HALF_OPEN ──(success)──▶ CLOSED
  ▲                                                           │
  └───────────────────(more failures)─────────────────────────┘
```

**States**:
- **CLOSED**: Normal operation, requests pass through
- **OPEN**: Service failing, requests rejected immediately (fast-fail)
- **HALF_OPEN**: Testing if service recovered, allow limited requests

**Configuration**:
```bash
# PeerDB circuit breaker
PEERDB_FAILURE_THRESHOLD=5    # Failures before opening
PEERDB_SUCCESS_THRESHOLD=2    # Successes to close circuit
PEERDB_TIMEOUT=60            # Seconds before HALF_OPEN

# PostgreSQL circuit breaker
POSTGRES_FAILURE_THRESHOLD=3
POSTGRES_SUCCESS_THRESHOLD=2
POSTGRES_TIMEOUT=30
```

**Benefits**:
- Prevents cascading failures
- Conserves resources during outages
- Automatic recovery detection
- Observable via `/metrics` endpoint

---

## Data Consistency

### Automatic Verification

Workers automatically verify data consistency after mirror creation:

```python
# Process:
1. Mirror created successfully
2. Wait 5 seconds for initial replication
3. Compare PostgreSQL row count vs ClickHouse row count
4. If match: Success
5. If mismatch: Retry up to 3 times with 10s delay
6. If still failing: Log error, continue monitoring
```

### Consistency Checker Service

Standalone service for scheduled and on-demand consistency checks:

```bash
# Start automatically with docker compose
docker compose up -d consistency-checker

# Check service health
curl http://localhost:8090/health
```

**HTTP Endpoints**:

| Endpoint | Purpose | Example |
|----------|---------|---------|
| `GET /health` | Service health | `curl localhost:8090/health` |
| `GET /verify?schema=X&table=Y` | Verify specific table | `curl "localhost:8090/verify?schema=public&table=users"` |
| `GET /verify-all` | Verify all mirrors | `curl localhost:8090/verify-all` |

**Response Format**:
```json
{
  "table": "public.users",
  "postgres_count": 1000,
  "clickhouse_count": 1000,
  "match": true,
  "difference": 0,
  "timestamp": "2025-01-12T10:30:00Z"
}
```

**Scheduled Checks**:
Runs automatically every 15 minutes (configurable):

```bash
# .env configuration
CONSISTENCY_CHECK_INTERVAL=900  # 15 minutes in seconds
```

---

## Configuration

### Environment Variables

Complete reference in `.env.example`. Key variables:

#### Database Connections
```bash
# PostgreSQL (Source)
POSTGRES_HOST=postgres          # Remote: your-pg.example.com
POSTGRES_PORT=5432
POSTGRES_USER=echodb
POSTGRES_PASSWORD=password
POSTGRES_DB=echodb

# ClickHouse (Target)
CLICKHOUSE_HOST=clickhouse      # Remote: your-ch.example.com
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=echodb
CLICKHOUSE_PASSWORD=password
CLICKHOUSE_DATABASE=echodb
```

#### Auto-Mirror Settings
```bash
SYNC_SCHEMA=public              # Comma-separated: "public,analytics,logs"
EXCLUDED_TABLES=spatial_ref_sys,geometry_columns
SOURCE_PEER_NAME=postgres_main
TARGET_PEER_NAME=clickhouse_analytics
```

#### High Availability
```bash
WORKER_ID=worker-1              # Unique per instance (auto-generated)
LEADER_ELECTION_TTL=30
LEADER_ELECTION_INTERVAL=10
```

#### Circuit Breakers
```bash
PEERDB_FAILURE_THRESHOLD=5
PEERDB_SUCCESS_THRESHOLD=2
PEERDB_TIMEOUT=60
```

#### Consistency Checker
```bash
CONSISTENCY_CHECK_INTERVAL=900
CONSISTENCY_CHECKER_PORT=8090
```

### Custom Configuration Examples

**Development (Low Resources)**:
```bash
MAX_RETRIES=2
CONSISTENCY_CHECK_INTERVAL=3600  # 1 hour
LEADER_ELECTION_TTL=60
```

**Production (High Availability)**:
```bash
MAX_RETRIES=10
RETRY_BACKOFF=2.0
LEADER_ELECTION_TTL=15
PEERDB_FAILURE_THRESHOLD=3
CONSISTENCY_CHECK_INTERVAL=300  # 5 minutes
```

---

## Monitoring

### Health Check Endpoints

```bash
# Worker-1 (Leader or Follower)
curl http://localhost:8080/health
curl http://localhost:8080/ready
curl http://localhost:8080/metrics

# Worker-2 (Leader or Follower)
curl http://localhost:8081/health
curl http://localhost:8081/ready
curl http://localhost:8081/metrics

# Consistency Checker
curl http://localhost:8090/health
curl "http://localhost:8090/verify?schema=public&table=users"
```

**Metrics Response**:
```json
{
  "running": true,
  "connected": true,
  "worker_id": "worker-1",
  "is_leader": true,
  "mirrors_created": 15,
  "mirrors_dropped": 2,
  "mirrors_failed": 0,
  "last_error": null,
  "uptime_seconds": 86400,
  "circuit_breakers": {
    "peerdb_api": {
      "state": "closed",
      "failures": 0
    },
    "postgres_connection": {
      "state": "closed",
      "failures": 0
    }
  }
}
```

### Logs

```bash
# Worker logs
docker logs echodb-auto-mirror-worker-1 -f
docker logs echodb-auto-mirror-worker-2 -f

# Consistency checker logs
docker logs echodb-consistency-checker-1 -f

# Auto-setup logs (one-time setup)
docker logs echodb-auto-setup-1
```

### PeerDB UI

Access at http://localhost:3000 (or `YOUR_VM_IP:3000` for remote deployment):

- View all mirrors and status
- Monitor replication lag
- Check row counts
- View sync errors

### Temporal UI

Access at http://localhost:8085:

- Monitor workflow execution
- Debug failed workflows
- View worker task history

---

## Troubleshooting

### Deployment Issues

**Problem**: Services fail to start
```bash
# Check if ports are already in use
netstat -tuln | grep -E '5432|8123|6379|3000|8080|8081|8090'

# Check Docker logs
docker compose logs

# Verify environment file
cat .env
```

**Problem**: Auto-setup fails
```bash
# Check auto-setup logs
docker logs echodb-auto-setup-1

# Manually retry setup
docker compose up -d auto-setup
```

### Connection Issues

**Problem**: Cannot connect to remote database
```bash
# Test connection from Docker container
docker run --rm --network echodb-network postgres:17-alpine \
  psql -h YOUR_REMOTE_HOST -U YOUR_USER -d YOUR_DB -c "SELECT 1;"

# Check firewall rules
telnet YOUR_REMOTE_HOST 5432

# Verify PostgreSQL pg_hba.conf allows your IP
# On remote PostgreSQL:
# host    all    all    YOUR_ECHODB_IP/32    md5
```

**Problem**: Workers cannot connect to databases
```bash
# Check worker readiness
curl http://localhost:8080/ready
# Should return: {"ready": true}

# Check worker logs
docker logs echodb-auto-mirror-worker-1 | grep -i error

# Verify database connectivity
docker exec echodb-auto-mirror-worker-1 python -c "
import psycopg2
conn = psycopg2.connect(
    host='postgres',
    port=5432,
    user='echodb',
    password='password',
    database='echodb'
)
print('PostgreSQL: OK')
conn.close()
"
```

### Auto-Mirror Issues

**Problem**: Mirror not created for new table
```bash
# 1. Check if trigger is installed
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  SELECT * FROM pg_event_trigger WHERE evtname = 'peerdb_auto_mirror_trigger';
"

# 2. Check worker is leader
curl http://localhost:8080/metrics | grep is_leader
# Should show: "is_leader": true

# 3. Check for duplicate prevention
docker exec echodb-redis-1 redis-cli -a password KEYS "echodb:auto_mirror:notification:*"

# 4. Check circuit breaker state
curl http://localhost:8080/metrics | grep -A 5 circuit_breakers
```

**Problem**: Both workers processing same notification
```bash
# Check Redis is working
docker exec echodb-redis-1 redis-cli -a password PING

# Check duplicate prevention keys
docker exec echodb-redis-1 redis-cli -a password KEYS "echodb:auto_mirror:notification:*"

# Verify leader election
docker exec echodb-redis-1 redis-cli -a password GET echodb:auto_mirror:leader_lock
```

### Data Consistency Issues

**Problem**: Row counts don't match
```bash
# Run on-demand consistency check
curl "http://localhost:8090/verify?schema=public&table=users"

# Check replication lag in PeerDB UI
# http://localhost:3000

# Verify mirror is active
docker exec echodb-peerdb-1 psql -h peerdb -U echodb -d echodb -c "
  SELECT name, flow_status FROM peerdb.mirrors;
"

# Check flow worker logs
docker logs echodb-flow-worker-1 | grep -i error
```

**Problem**: Replication lag is high
```bash
# Check ClickHouse server load
docker exec echodb-clickhouse-1 clickhouse-client --query "
  SELECT * FROM system.metrics WHERE metric LIKE '%Replica%';
"

# Check network latency
docker exec echodb-auto-mirror-worker-1 ping -c 3 postgres
docker exec echodb-auto-mirror-worker-1 ping -c 3 clickhouse

# Verify MinIO staging (should not accumulate)
curl http://localhost:9002  # MinIO console
```

### Circuit Breaker Issues

**Problem**: Circuit breaker stuck open
```bash
# Check circuit state
curl http://localhost:8080/metrics | grep -A 3 circuit_breakers

# Manually reset by restarting worker
docker compose restart auto-mirror-worker-1

# Verify PeerDB is healthy
docker exec echodb-peerdb-1 psql -h peerdb -U echodb -d echodb -c "SELECT 1;"
```

### Leader Election Issues

**Problem**: No leader elected
```bash
# Check Redis
docker exec echodb-redis-1 redis-cli -a password GET echodb:auto_mirror:leader_lock

# Check worker logs for leader election
docker logs echodb-auto-mirror-worker-1 | grep -i leader
docker logs echodb-auto-mirror-worker-2 | grep -i leader

# Verify Redis connectivity
docker exec echodb-auto-mirror-worker-1 python -c "
import redis
client = redis.Redis(host='redis', port=6379, password='password', decode_responses=True)
print(client.ping())
"
```

### Getting Help

1. **Check logs**: All services have comprehensive logging
2. **Check health endpoints**: Use `/health` and `/metrics` endpoints
3. **Check PeerDB UI**: Visual mirror status at http://localhost:3000
4. **Check Temporal UI**: Workflow status at http://localhost:8085
5. **Review this manual**: Common issues are documented above
6. **GitHub Issues**: Check for known issues or report new ones

---

## Best Practices

### Production Deployment

1. **Change all default passwords**
2. **Use SSL/TLS for remote database connections**
3. **Configure firewall rules to restrict access**
4. **Enable log aggregation** (ELK, CloudWatch, etc.)
5. **Set up monitoring alerts** for circuit breaker events
6. **Use 3+ worker instances** for true HA
7. **Configure backup strategy** for PostgreSQL and ClickHouse
8. **Test failover procedures** before production
9. **Use separate Redis cluster** for production
10. **Monitor disk usage** on MinIO (CDC staging)

### Security Checklist

- [ ] Change default passwords
- [ ] Restrict database port access (5432, 8123, 9000)
- [ ] Use HTTPS for web UIs (PeerDB, Temporal)
- [ ] Enable PostgreSQL SSL for remote connections
- [ ] Configure ClickHouse authentication
- [ ] Use Redis password
- [ ] Restrict MinIO console access
- [ ] Enable audit logging
- [ ] Rotate credentials regularly
- [ ] Use secrets manager (AWS Secrets Manager, HashiCorp Vault)

### Performance Tuning

1. **ClickHouse**: Increase memory allocation for large datasets
2. **PostgreSQL**: Adjust `max_wal_senders` and `max_replication_slots`
3. **MinIO**: Use local disk instead of network storage
4. **Workers**: Increase `MAX_RETRIES` for unreliable networks
5. **Circuit breakers**: Adjust thresholds based on SLA requirements

---

For implementation details and architecture decisions, see [README.md](./README.md).
