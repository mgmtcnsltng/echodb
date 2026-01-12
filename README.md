# EchoDB - PostgreSQL to ClickHouse CDC with PeerDB

Complete Docker setup for real-time data replication from PostgreSQL to ClickHouse using PeerDB with production-ready features:

- **Zero-Setup Deployment** - Works immediately after `docker compose up`
- **High Availability** - Leader election with multiple worker instances
- **Circuit Breakers** - Prevents cascading failures
- **Auto-Mirror** - Automatically creates mirrors for new PostgreSQL tables
- **Data Consistency** - Automatic verification between PostgreSQL and ClickHouse
- **Multi-Schema Support** - Watch multiple PostgreSQL schemas
- **DROP TABLE Handling** - Automatically drops mirrors when tables are dropped
- **Flexible Deployment** - Local, remote VM, or remote database connections

## Features

### Core CDC Features
- **Real-time Change Data Capture** from PostgreSQL to ClickHouse
- **Automatic Schema Sync** - New tables replicated instantly
- **DROP TABLE Support** - Mirrors automatically dropped when tables deleted
- **Multi-Schema Watch** - Monitor multiple schemas simultaneously

### Production-Ready Features
- **High Availability** - Redis-based leader election with 2+ worker instances
- **Circuit Breaker Pattern** - Fast-fail and automatic recovery
- **Duplicate Prevention** - Redis-based notification deduplication
- **Data Consistency Verification** - Row count verification with retries
- **Retry Logic** - Exponential backoff for failed operations
- **Auto-Reconnect** - Automatic database reconnection
- **Health Checks** - HTTP endpoints for all services

### Deployment Flexibility
- **Local Deployment** - Everything runs in Docker on your machine
- **Remote VM Deployment** - Deploy on any server with Docker
- **Remote PostgreSQL** - Connect to remote PostgreSQL server
- **Remote ClickHouse** - Connect to remote ClickHouse server
- **Hybrid** - Any combination of local and remote databases

## Quick Start

### 1. Clone and Configure

```bash
git clone <repo-url>
cd echodb
cp .env.example .env
# Edit .env if needed (defaults work for local deployment)
```

### 2. Start Services

```bash
docker compose up -d
```

That's it! **Zero manual setup required** - auto-setup runs automatically on first startup.

### 3. Verify Deployment

```bash
# Check all services are healthy
docker compose ps

# Check worker health
curl http://localhost:8080/health  # Worker-1
curl http://localhost:8081/health  # Worker-2
curl http://localhost:8090/health  # Consistency Checker
```

### 4. Create a Test Table

```bash
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT
  );
"
```

### 5. Verify Auto-Mirror

```bash
# Check worker metrics
curl http://localhost:8080/metrics

# Query ClickHouse for replicated data
docker exec echodb-clickhouse-1 clickhouse-client --query "
  SELECT * FROM postgres.users LIMIT 10;
"
```

## Deployment Scenarios

EchoDB supports three deployment scenarios:

### Scenario 1: Local Deployment (Default)

Everything runs on your local machine in Docker containers.

**Configuration**: Use default `.env` file

```bash
cp .env.example .env
docker compose up -d
```

**What you get**:
- PostgreSQL on port 5432
- ClickHouse on port 8123
- PeerDB UI on port 3000
- Auto-mirror workers on ports 8080, 8081
- Consistency checker on port 8090

**Use cases**: Development, testing, demonstrations

---

### Scenario 2: Remote VM Deployment

Deploy EchoDB on a remote server (cloud VM, dedicated server).

**Configuration**: Default `.env` works as-is

```bash
# On remote VM
cp .env.example .env
docker compose up -d

# From your local machine
ssh user@your-vm-ip
# Or access services via http://your-vm-ip:3000
```

**What you get**:
- All services on remote VM
- Access via VM's IP address
- Auto-setup runs automatically
- Zero manual configuration

**Use cases**: Production deployment, team sharing, cloud hosting

**Security recommendations**:
- Change all default passwords
- Use reverse proxy (nginx/traefik) for web UIs
- Restrict database port access via firewall
- Use VPN or SSH tunnel for admin access

---

### Scenario 3: Remote Database Connections

EchoDB runs locally or on VM, but connects to remote PostgreSQL and/or ClickHouse.

#### 3A. Remote PostgreSQL Only

```bash
# Edit .env
POSTGRES_HOST=your-postgres.example.com
POSTGRES_PORT=5432
POSTGRES_USER=your_remote_user
POSTGRES_PASSWORD=your_remote_password
POSTGRES_DB=your_remote_db

# ClickHouse remains local
CLICKHOUSE_HOST=clickhouse

# Start services
docker compose up -d
```

**Use cases**: Centralized PostgreSQL, local analytics

#### 3B. Remote ClickHouse Only

```bash
# PostgreSQL remains local
POSTGRES_HOST=postgres

# Edit .env for remote ClickHouse
CLICKHOUSE_HOST=your-clickhouse.example.com
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=your_remote_user
CLICKHOUSE_PASSWORD=your_remote_password
CLICKHOUSE_DATABASE=your_remote_db

# Start services
docker compose up -d
```

**Use cases**: Local PostgreSQL, centralized ClickHouse cluster

#### 3C. Both Remote PostgreSQL and ClickHouse

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

# Start services
docker compose up -d
```

**Use cases**: CDC bridge between two remote databases, multi-cloud deployments

**Network requirements**:
- EchoDB host must have network access to both remote databases
- Firewalls must allow connections from EchoDB host IP
- Consider using VPN for secure connections
- Monitor network latency

---

## Architecture

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

## Services

| Service | Port | Description | Health Check |
|---------|------|-------------|--------------|
| **PostgreSQL** | 5432 | Application database (source) | - |
| **ClickHouse** | 8123, 9000 | Analytics database (target) | - |
| **Redis** | 6379 | Distributed locks and caching | - |
| **PeerDB Server** | 9900 | ETL/ELT SQL interface | - |
| **PeerDB UI** | 3000 | Web dashboard | http://localhost:3000 |
| **PeerDB Flow API** | 8112, 8113 | gRPC/HTTP API | - |
| **Temporal** | 7233 | Workflow orchestration | - |
| **Temporal UI** | 8085 | Temporal dashboard | http://localhost:8085 |
| **MinIO** | 9001, 9002 | S3-compatible storage | http://localhost:9002 |
| **Auto-Mirror Worker 1** | 8080 | Worker instance 1 | http://localhost:8080/health |
| **Auto-Mirror Worker 2** | 8081 | Worker instance 2 | http://localhost:8081/health |
| **Consistency Checker** | 8090 | Data verification | http://localhost:8090/health |
| **Auto-Setup** | - | One-time setup service | - |

## Environment Variables

Complete reference in `.env.example`. Key variables:

### Database Connections

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

### Auto-Mirror Configuration

```bash
# Schema(s) to watch (comma-separated)
SYNC_SCHEMA=public              # Multiple: "public,analytics,logs"

# Tables to exclude from auto-mirror
EXCLUDED_TABLES=spatial_ref_sys,geometry_columns,geography_columns

# Source and target peer names
SOURCE_PEER_NAME=postgres_main
TARGET_PEER_NAME=clickhouse_analytics
```

### High Availability Configuration

```bash
# Leader election
WORKER_ID=worker-1              # Unique per instance (auto-generated)
LEADER_ELECTION_TTL=30          # Leadership lease (seconds)
LEADER_ELECTION_INTERVAL=10     # Retry interval when follower

# Circuit breakers
PEERDB_FAILURE_THRESHOLD=5      # Failures before opening circuit
PEERDB_SUCCESS_THRESHOLD=2      # Successes to close circuit
PEERDB_TIMEOUT=60              # Seconds before HALF_OPEN state
```

### Data Consistency Configuration

```bash
# Consistency checker
CONSISTENCY_CHECK_INTERVAL=900  # 15 minutes (seconds)
CONSISTENCY_CHECKER_PORT=8090   # HTTP API port
```

## Usage Examples

### Auto-Mirror (Recommended)

Create a new table and it's automatically replicated:

```bash
# Create table in PostgreSQL
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    price DECIMAL(10, 2)
  );
"

# Mirror is created automatically by worker
# Data appears in ClickHouse within seconds

# Verify in ClickHouse
docker exec echodb-clickhouse-1 clickhouse-client --query "
  SELECT * FROM postgres.products;
"
```

### Multi-Schema Support

Watch multiple schemas:

```bash
# .env configuration
SYNC_SCHEMA=public,analytics,logs

# Create table in any watched schema
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  CREATE TABLE analytics.events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT,
    timestamp TIMESTAMP DEFAULT NOW()
  );
"

# Mirror created automatically
```

### DROP TABLE Handling

Drop a table and mirror is removed:

```bash
# Drop table in PostgreSQL
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  DROP TABLE products;
"

# Mirror is dropped automatically
# Verify: curl http://localhost:8080/metrics
```

### Manual Mirror Creation

You can still create mirrors manually via PeerDB:

```bash
# Connect to PeerDB
docker exec -it echodb-peerdb-1 psql -h peerdb -U echodb -d echodb

# Create mirror
CREATE MIRROR custom_mirror
FROM postgres_main
TO clickhouse_analytics
WITH TABLE MAPPING (public.custom_table:custom_table)
WITH (do_initial_copy = true);
```

## Monitoring

### Health Check Endpoints

```bash
# Worker-1
curl http://localhost:8080/health    # Liveness
curl http://localhost:8080/ready     # Readiness
curl http://localhost:8080/metrics   # Detailed metrics

# Worker-2
curl http://localhost:8081/health
curl http://localhost:8081/metrics

# Consistency Checker
curl http://localhost:8090/health

# On-demand consistency check
curl "http://localhost:8090/verify?schema=public&table=users"
```

### Metrics Response

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

# Auto-setup logs (one-time)
docker logs echodb-auto-setup-1

# PeerDB logs
docker logs echodb-flow-worker-1 -f
```

### Web UIs

- **PeerDB UI**: http://localhost:3000 (or `YOUR_VM_IP:3000`)
- **Temporal UI**: http://localhost:8085 (or `YOUR_VM_IP:8085`)
- **MinIO Console**: http://localhost:9002 (or `YOUR_VM_IP:9002`)

## Troubleshooting

### Deployment Issues

**Problem**: Services fail to start
```bash
# Check if ports are in use
netstat -tuln | grep -E '5432|8123|6379|3000|8080|8081|8090'

# Check logs
docker compose logs

# Verify environment
cat .env
```

### Connection Issues

**Problem**: Cannot connect to remote database
```bash
# Test connection from container
docker run --rm --network echodb-network postgres:17-alpine \
  psql -h YOUR_REMOTE_HOST -U YOUR_USER -d YOUR_DB -c "SELECT 1;"

# Check firewall
telnet YOUR_REMOTE_HOST 5432

# Verify pg_hba.conf on remote PostgreSQL
# Should allow connections from EchoDB host IP
```

**Problem**: Workers not processing notifications
```bash
# Check if trigger is installed
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  SELECT * FROM pg_event_trigger WHERE evtname = 'peerdb_auto_mirror_trigger';
"

# Check worker is leader
curl http://localhost:8080/metrics | grep is_leader

# Check circuit breaker state
curl http://localhost:8080/metrics | grep -A 5 circuit_breakers
```

### Data Consistency Issues

**Problem**: Row counts don't match
```bash
# Run on-demand check
curl "http://localhost:8090/verify?schema=public&table=users"

# Check mirror status
docker exec echodb-peerdb-1 psql -h peerdb -U echodb -d echodb -c "
  SELECT name, flow_status FROM peerdb.mirrors;
"

# Check replication lag in PeerDB UI
# http://localhost:3000
```

### Getting Help

1. **Check logs**: `docker logs <service-name> -f`
2. **Check health endpoints**: `curl http://localhost:8080/metrics`
3. **Check PeerDB UI**: http://localhost:3000
4. **Check Temporal UI**: http://localhost:8085
5. **Read USER_MANUAL.md**: Comprehensive troubleshooting guide
6. **GitHub Issues**: Report bugs or request features

## Production Deployment

### Security Checklist

- [ ] Change all default passwords in `.env`
- [ ] Restrict database port access (5432, 8123, 9000) via firewall
- [ ] Use HTTPS for web UIs (PeerDB, Temporal)
- [ ] Enable PostgreSQL SSL for remote connections
- [ ] Configure ClickHouse authentication properly
- [ ] Use Redis password
- [ ] Restrict MinIO console access
- [ ] Enable audit logging
- [ ] Rotate credentials regularly
- [ ] Use secrets manager (AWS Secrets Manager, HashiCorp Vault)

### Performance Tuning

```bash
# .env recommendations for production

# Increase retry attempts for unreliable networks
MAX_RETRIES=10
RETRY_BACKOFF=2.0

# Faster leader failover
LEADER_ELECTION_TTL=15

# More aggressive circuit breakers
PEERDB_FAILURE_THRESHOLD=3

# Frequent consistency checks
CONSISTENCY_CHECK_INTERVAL=300  # 5 minutes
```

### Backup Strategy

```bash
# Backup PostgreSQL
docker exec echodb-postgres-1 pg_dump -U echodb echodb > backup.sql

# Backup ClickHouse
docker exec echodb-clickhouse-1 clickhouse-client --query "
  BACKUP TABLE postgres.* TO Disk('backups', 'latest')
"
```

### Scaling

- **3+ Workers**: Add more worker instances for true HA
- **Separate Redis**: Use managed Redis (ElastiCache, Redis Cloud)
- **Separate MinIO**: Use S3 or managed MinIO
- **ClickHouse Cluster**: Use ClickHouse cluster for scalability

## Documentation

- **[USER_MANUAL.md](./USER_MANUAL.md)** - Complete user guide with deployment scenarios
- **[CLAUDE.md](./CLAUDE.md)** - AI assistant context and codebase overview
- **[skill.md](./skill.md)** - PeerDB SQL reference

## License

This is a configuration template. Refer to individual component licenses:
- PeerDB: Elastic License 2.0 (ELv2)
- PostgreSQL: PostgreSQL License
- ClickHouse: Apache License 2.0
- Temporal: MIT License

## Resources

- [PeerDB Documentation](https://docs.peerdb.io)
- [PeerDB GitHub](https://github.com/PeerDB-io/peerdb)
- [ClickHouse Docs](https://clickhouse.com/docs)
- [Temporal Docs](https://temporal.io/docs)

## Contributing

Contributions welcome! Please read:
1. USER_MANUAL.md for feature overview
2. CLAUDE.md for codebase architecture
3. Existing scripts for code patterns

## Support

- **Issues**: [GitHub Issues](https://github.com/your-repo/echodb/issues)
- **Documentation**: USER_MANUAL.md
- **PeerDB Community**: [PeerDB Discord](https://discord.gg/peerdb)
