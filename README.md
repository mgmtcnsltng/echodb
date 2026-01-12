# EchoDB - Automated PostgreSQL to ClickHouse CDC with PeerDB

Complete **zero-touch** Docker-based CDC solution for real-time data replication from PostgreSQL to ClickHouse using PeerDB with production-ready features:

### Core Value Propositions

- **True Zero-Touch Deployment** - Works immediately after `docker compose up -d`
- **Full Automation** - Auto-creates peers, triggers, mirrors, and cleans databases
- **Complete Auto-Mirror** - Automatically creates mirrors for ALL PostgreSQL tables (including init tables)
- **Production-Ready** - HA, circuit breakers, retries, health checks, and monitoring built-in

### Technical Features

- **Real-Time CDC** - Logical replication for sub-second data sync
- **High Availability** - Redis-based leader election with multiple worker instances
- **Circuit Breakers** - Prevents cascading failures with automatic recovery
- **Data Consistency** - Automatic verification between PostgreSQL and ClickHouse
- **DROP TABLE Handling** - Mirrors automatically dropped when tables deleted
- **Clean Databases** - Automatically removes unwanted `postgres` and `default` databases
- **Multi-Schema Support** - Watch multiple PostgreSQL schemas simultaneously (comma-separated)

### Deployment Flexibility

- **Local Deployment** - Everything runs in Docker on your machine
- **Remote VM Deployment** - Deploy on any server with Docker
- **Remote PostgreSQL** - Connect to remote PostgreSQL server
- **Remote ClickHouse** - Connect to remote ClickHouse server
- **Hybrid** - Any combination of local and remote databases
- **Cloud Ready** - Works with AWS RDS, Google Cloud SQL, Azure Database

## Quick Start

### 1. Clone and Configure

```bash
git clone https://github.com/mgmtcnsltng/echodb.git
cd echodb
cp .env.example .env
# Edit .env if needed (defaults work for local deployment)
```

### 2. Start Services

```bash
docker compose up -d
```

**That's it!** Everything is automated:
- ✅ PostgreSQL and ClickHouse initialized
- ✅ PeerDB peers created automatically
- ✅ Auto-mirror triggers installed
- ✅ Init tables (users, products, orders, order_items) mirrored automatically
- ✅ Unwanted databases (`postgres`, `default`) removed
- ✅ Workers start listening for new tables

### 3. Verify Deployment

```bash
# Check all services are healthy
docker compose ps

# Check worker health
curl http://localhost:8080/health  # Worker-1
curl http://localhost:8081/health  # Worker-2

# Check databases
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "\l"  # No 'postgres' DB
docker exec echodb-clickhouse-1 clickhouse-client --query "SHOW DATABASES"  # No 'default' DB
```

### 4. Test Auto-Mirror

```bash
# Create a new table
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  CREATE TABLE test_table (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
  );
  
  INSERT INTO test_table (name) VALUES ('Alice'), ('Bob'), ('Charlie');
"

# Verify mirror was created automatically
docker logs echodb-auto-mirror-worker-1-1 --tail 20 | grep "test_table"

# Check data in ClickHouse
docker exec echodb-clickhouse-1 clickhouse-client --query "
  SELECT count(*) FROM echodb.test_table;
"
```

## What Happens Automatically

### On First Startup (`docker compose up -d`)

1. **PostgreSQL initializes** with sample tables (users, products, orders, order_items)
2. **ClickHouse initializes** with clean databases
3. **Auto-setup runs:**
   - Creates PeerDB peers (postgres_main, clickhouse_analytics)
   - Installs PostgreSQL event triggers
   - Creates mirrors for init tables
   - Removes unwanted databases
4. **Auto-mirror workers start:**
   - Elect leader via Redis
   - Begin listening for table events
5. **Data replication begins** for all tables

### When You Create a New Table

1. You run `CREATE TABLE` in PostgreSQL
2. Event trigger fires automatically
3. Worker receives notification via LISTEN/NOTIFY
4. Worker creates PeerDB mirror
5. Data replicates to ClickHouse via CDC

**Zero manual steps required!** 🎉

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EchoDB Architecture                               │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────┐         ┌──────────────┐         ┌──────────────┐
  │  PostgreSQL  │         │    PeerDB    │         │  ClickHouse  │
  │  (echodb)    │────────▶│   (ETL/ELT)  │────────▶│  (echodb)    │
  │              │   CDC   │              │  Sync   │              │
  └──────┬───────┘         └──────┬───────┘         └──────────────┘
         │                       │
         │ pg_notify()            │
         ▼                       ▼
  ┌──────────────┐         ┌──────────────┐
  │ Event Trigger│         │    MinIO     │
  │  (Auto Hook) │         │  (Staging)   │
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
  │  • Automatic mirror creation for new tables                │
  │  • Automatic mirror creation for init tables               │
  │  • DROP TABLE handling (mirrors auto-dropped)              │
  │  • Leader election (Redis)                                  │
  │  • Circuit breakers                                         │
  │  • Retry logic with exponential backoff                     │
  │  • Auto-reconnect                                           │
  │  • Duplicate prevention                                     │
  │  • Health check endpoints                                   │
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
| **Auto-Setup** | - | One-time setup service | Runs once on startup |

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
CLICKHOUSE_PORT=9000            # Native protocol port
CLICKHOUSE_USER=echodb
CLICKHOUSE_PASSWORD=password
CLICKHOUSE_DATABASE=echodb
```

### Auto-Mirror Configuration

```bash
# Schema(s) to watch (comma-separated)
SYNC_SCHEMA=public              # Multiple: "public,analytics,logs"

# Tables to exclude from auto-mirror
EXCLUDED_TABLES=spatial_ref_sys,geometry_columns,geography_columns,raster_columns,raster_overviews

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

### Web UIs

- **PeerDB UI**: http://localhost:3000 - View and manage mirrors
- **Temporal UI**: http://localhost:8085 - Monitor workflow executions
- **MinIO Console**: http://localhost:9002 - View staged CDC data

### Logs

```bash
# Worker logs
docker logs echodb-auto-mirror-worker-1-1 -f
docker logs echodb-auto-mirror-worker-2-1 -f

# Consistency checker logs
docker logs echodb-consistency-checker-1 -f

# Auto-setup logs (one-time)
docker logs echodb-auto-setup-1

# PeerDB logs
docker logs echodb-flow-worker-1 -f
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

**Use cases**: Development, testing, demonstrations

---

### Scenario 2: Remote VM Deployment

Deploy EchoDB on a remote server (cloud VM, dedicated server).

**Configuration**: Default `.env` works as-is

```bash
# On remote VM
git clone <repo-url>
cd echodb
cp .env.example .env
docker compose up -d

# From your local machine - access services via VM IP
curl http://YOUR_VM_IP:8080/health
```

**Use cases**: Production deployment, team sharing, cloud hosting

**Security recommendations**:
- Change all default passwords in `.env`
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
CLICKHOUSE_PORT=9000
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
CLICKHOUSE_PORT=9000
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

## Troubleshooting

### Auto-Mirror Not Working

**Problem**: New tables not being mirrored

```bash
# Check if trigger is installed
docker exec echodb-postgres-1 psql -U echodb -d echodb -c "
  SELECT evtname FROM pg_event_trigger;
"

# Should show: peerdb_auto_mirror_trigger, peerdb_auto_mirror_drop_trigger

# Check worker is leader
curl http://localhost:8080/metrics | grep is_leader

# Check worker logs for notifications
docker logs echodb-auto-mirror-worker-1-1 --tail 50
```

### Mirror Created But No Data

**Problem**: Mirror exists but data not replicating

```bash
# Check mirror status in catalog
docker exec echodb-catalog-1 psql -U postgres -d postgres -c "
  SELECT id, name, flow_status FROM flows;
"

# Check flow-worker logs
docker logs echodb-flow-worker-1 --tail 50

# Common issues:
# - Table has no primary key (required for CDC)
# - REPLICA IDENTITY not set
# - Replication slot issues
```

### Connection Issues

**Problem**: Cannot connect to remote database

```bash
# Test connection from container
docker run --rm --network echodb-network postgres:17-alpine \
  psql -h YOUR_REMOTE_HOST -U YOUR_USER -d YOUR_DB -c "SELECT 1;"

# Check firewall
telnet YOUR_REMOTE_HOST 5432
```

### Getting Help

1. **Check logs**: `docker logs <service-name> -f`
2. **Check health endpoints**: `curl http://localhost:8080/metrics`
3. **Check PeerDB UI**: http://localhost:3000
4. **Read USER_MANUAL.md**: Comprehensive troubleshooting guide
5. **GitHub Issues**: Report bugs or request features

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
  BACKUP TABLE echodb.* TO Disk('backups', 'latest')
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

## License

This is a configuration template. Refer to individual component licenses:
- PeerDB: Elastic License 2.0 (ELv2)
- PostgreSQL: PostgreSQL License
- ClickHouse: Apache License 2.0
- Temporal: MIT License

## Credits & Acknowledgments

This project is built upon amazing open-source technologies:

- **[PeerDB](https://peerdb.io)** - ETL/ELT platform for PostgreSQL CDC
  - License: Elastic License 2.0 (ELv2)
  - GitHub: https://github.com/PeerDB-io/peerdb
  
- **[PostgreSQL](https://www.postgresql.org)** - World's most advanced open source relational database
  - License: PostgreSQL License
  - Website: https://www.postgresql.org
  
- **[ClickHouse](https://clickhouse.com)** - Fast open-source OLAP database management system
  - License: Apache License 2.0
  - GitHub: https://github.com/ClickHouse/ClickHouse
  
- **[Temporal](https://temporal.io)** - Platform for durable execution
  - License: MIT License
  - GitHub: https://github.com/temporalio/temporal

Additional components:
- **[Redis](https://redis.io)** - In-memory data structure store
- **[MinIO](https://min.io)** - High-performance object storage
- **[Temporal](https://temporal.io)** - Workflow orchestration platform

### AI Development Tools

This project was developed with assistance from advanced AI coding assistants:

- **[Claude Code](https://claude.ai/code)** - Anthropic's AI-powered development environment
- **[OpenAI Codex](https://openai.com)** - AI code generation and assistance
- **[GLM-4.7](https://z.ai/blog/glm-4.7)** - Open-source language model by Zhipu AI

### Development Tools

- **[Zed](https://zed.dev)** - High-performance code editor by Zed Industries
- **[Docker](https://www.docker.com)** - Container platform for building and shipping applications

Special thanks to the open-source community and AI research community for making these powerful tools available.

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
