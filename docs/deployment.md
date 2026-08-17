# Deployment Guide

RetailMind AI deployment - Docker Compose orchestration, production hardening, TLS configuration, and environment management.

**Last Updated**: 2026-08-15
**Version**: 0.9.0

---

## Quick Start

### Development

```bash
# Clone repository
git clone https://github.com/org/retailmind-ai.git
cd retailmind-ai

# Copy environment template
cp .env.example .env

# Start development stack
make up

# Access services
# - UI: http://localhost:8501
# - API: http://localhost:8000
# - API Docs: http://localhost:8000/docs
```

### Production

```bash
# Configure secrets
mkdir -p infra/secrets
echo "your-db-password" > infra/secrets/db_password
echo "your-jwt-private-key" > infra/secrets/jwt_private_key
chmod 600 infra/secrets/*

# Start production stack
docker compose -f infra/compose/compose.yml \
               -f infra/compose/compose.prod.yml \
               up -d

# Verify
docker compose ps
docker compose logs -f backend-api
```

---

## Architecture

### Service Topology

```
┌─────────────────── EDGE NETWORK ──────────────────┐
│  nginx (443) → TLS termination                   │
└────────────────────┬──────────────────────────────┘
                     │
┌────────────────────▼─── APP NETWORK ─────────────┐
│  backend-api (8000) - FastAPI                    │
│  ui (8501) - Streamlit                           │
│  celery-worker - Background tasks                │
│  celery-beat - Scheduler                         │
└────────────────────┬──────────────────────────────┘
                     │
┌────────────────────▼─── DATA NETWORK ────────────┐
│  postgres (5432) - Application DB                │
│  redis-cache (6379) - Volatile cache             │
│  redis-state (6380) - Durable state              │
└───────────────────────────────────────────────────┘
```

---

## Configuration

### Environment Variables

**File**: `.env`

```bash
# Application
RM_APP_NAME=RetailMind AI
RM_APP_BASE_URL=https://retailmind.example.com
RM_ENV=production

# Database
RM_DB_HOST=postgres
RM_DB_PORT=5432
RM_DB_NAME=retailmind
RM_DB_PASSWORD_FILE=/run/secrets/db_password
RM_DB_SSLMODE=require

# Redis
RM_REDIS_CACHE_HOST=redis-cache
RM_REDIS_STATE_HOST=redis-state

# Auth (REQUIRED in production)
RM_AUTH_JWT_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key
RM_AUTH_JWT_PUBLIC_KEY_FILE=/run/secrets/jwt_public_key

# LLM (optional)
RM_LLM_PROVIDER=mock  # or "anthropic"
# RM_LLM_ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_api_key

# Rate Limiting
RM_RATE_LIMIT_ENABLED=true
RM_RATE_LIMIT_PER_IP=100/minute
```

---

## Production Hardening

### 1. Secrets Management

**File-based secrets** (not environment variables):

```bash
# Create secrets
mkdir -p infra/secrets
chmod 700 infra/secrets

# Database password
openssl rand -base64 32 > infra/secrets/db_password

# JWT keys (RS256 asymmetric)
openssl genrsa -out infra/secrets/jwt_private_key 2048
openssl rsa -in infra/secrets/jwt_private_key -pubout \
  -out infra/secrets/jwt_public_key

# LLM API key (if using Anthropic)
echo "sk-ant-..." > infra/secrets/anthropic_api_key

# Secure permissions
chmod 600 infra/secrets/*
```

### 2. TLS Configuration

**Generate Self-Signed Certificate** (dev/staging):

```bash
./scripts/generate_tls.sh

# Output:
# - infra/docker/nginx/tls/cert.pem
# - infra/docker/nginx/tls/key.pem
```

**Production Certificate** (Let's Encrypt):

```bash
# Use certbot
sudo certbot certonly --standalone \
  -d retailmind.example.com \
  -d api.retailmind.example.com

# Copy to nginx
cp /etc/letsencrypt/live/retailmind.example.com/fullchain.pem \
   infra/docker/nginx/tls/cert.pem
cp /etc/letsencrypt/live/retailmind.example.com/privkey.pem \
   infra/docker/nginx/tls/key.pem
```

### 3. Container Hardening

**File**: `infra/compose/compose.prod.yml`

```yaml
services:
  backend-api:
    read_only: true  # Read-only filesystem
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp:size=100M,mode=1777
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE
```

### 4. Network Isolation

**Production**: Only nginx exposed (port 443)

```yaml
# compose.prod.yml
# No published ports except nginx
services:
  nginx:
    ports:
      - "443:443"

  backend-api:
    # No ports published (internal only)
    networks:
      - app
```

---

## Database Migrations

```bash
# Check current version
docker compose exec backend-api alembic current

# Apply migrations
docker compose exec backend-api alembic upgrade head

# Rollback one migration
docker compose exec backend-api alembic downgrade -1
```

---

## Monitoring

### Prometheus Metrics

**Endpoint**: `http://localhost:8000/metrics`

**Key Metrics**:
- `http_requests_total{method, endpoint, status}`
- `http_request_duration_seconds{endpoint}`
- `cache_requests_total{domain, status}`
- `llm_requests_total{provider, status}`

### Grafana Dashboards

**File**: `infra/monitoring/grafana/dashboards/`

1. **API Dashboard** - Request rate, latency, errors
2. **Data Pipeline Dashboard** - Ingestion stats, quality gates
3. **BI Dashboard** - Cache hit rate, query latency

---

## Backup & Restore

### Automated Backups

**File**: `infra/scripts/backup.sh`

```bash
#!/bin/bash
# Daily PostgreSQL backup (cron: 0 3 * * *)

BACKUP_DIR=/var/backups/postgres
DATE=$(date +%Y%m%d_%H%M%S)

pg_dump -h postgres -U retailmind -F c -b -v \
  -f "${BACKUP_DIR}/retailmind_${DATE}.dump" \
  retailmind

gzip "${BACKUP_DIR}/retailmind_${DATE}.dump"

# Retain 30 days
find "${BACKUP_DIR}" -name "*.dump.gz" -mtime +30 -delete
```

### Restore

```bash
# List backups
ls -lh /var/backups/postgres/

# Restore
gunzip retailmind_20260815.dump.gz
pg_restore -h postgres -U retailmind -d retailmind_new \
  --clean --if-exists \
  retailmind_20260815.dump
```

---

## Scaling

### Current Limits

- Single server deployment
- File-based DuckDB warehouse
- Suitable for: <100GB data, <1000 concurrent users

### Horizontal Scaling (future)

1. **API Layer**: Multiple uvicorn workers behind load balancer
2. **Warehouse**: MotherDuck (distributed DuckDB) or Clickhouse
3. **Cache**: Redis Cluster
4. **Database**: PostgreSQL with read replicas

---

## Troubleshooting

### Common Issues

**1. "JWT verification failed"**

```bash
# Check JWT keys exist
ls -l infra/secrets/jwt_*

# Verify mounted in container
docker compose exec backend-api ls -l /run/secrets/
```

**2. "Database connection failed"**

```bash
# Check PostgreSQL is running
docker compose ps postgres

# Test connection
docker compose exec postgres psql -U retailmind -d retailmind -c "SELECT 1"
```

**3. "Rate limit exceeded"**

```bash
# Check Redis connection
docker compose exec redis-cache redis-cli ping

# Clear rate limit keys
docker compose exec redis-cache redis-cli --scan --pattern "rate_limit:*" | \
  xargs docker compose exec redis-cache redis-cli del
```

---

## Health Checks

```bash
# API health
curl http://localhost:8000/health

# Database health
curl http://localhost:8000/health/db

# Cache health
curl http://localhost:8000/health/cache

# LLM health (if enabled)
curl http://localhost:8000/health/llm
```

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
