# Hybrid Storage System - Configuration and Deployment Guide

## 🔧 Environment Configuration

### 1. Firebase Credentials Setup

#### Get Service Account JSON:
1. Go to Firebase Console: https://console.firebase.google.com/
2. Select your project (coconut-leaf-disease-dcf9a)
3. Go to Settings → Project Settings → Service Accounts
4. Click "Generate New Private Key"
5. Save the JSON file

#### Set Environment Variable:

**Linux/Mac:**
```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
python main_hybrid.py
```

**Windows (PowerShell):**
```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\path\to\service-account-key.json"
python main_hybrid.py
```

**Windows (Command Prompt):**
```cmd
set GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\service-account-key.json
python main_hybrid.py
```

**Persistent (Windows):**
```
Control Panel → System → Advanced System Settings → Environment Variables
Add: GOOGLE_APPLICATION_CREDENTIALS = C:\path\to\service-account-key.json
Restart computer or Python IDE
```

**Persistent (Linux/Mac - add to ~/.bashrc or ~/.zshrc):**
```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
```

### 2. Create .env File (Optional)

Create `.env` file in project root:
```
GOOGLE_APPLICATION_CREDENTIALS=./firebase-credentials.json
FIREBASE_PROJECT_ID=coconut-leaf-disease-dcf9a
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8000
SYNC_INTERVAL=60
LOG_LEVEL=INFO
```

Then load in Python:
```python
from dotenv import load_dotenv
import os

load_dotenv()
creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
```

---

## 🚀 Deployment Options

### Option 1: Local Development

```bash
# Terminal 1: Start API server
python main_hybrid.py

# Terminal 2 (optional): Monitor sync status
watch -n 5 'curl http://localhost:8000/storage/health | jq .'

# Terminal 3 (optional): View logs
tail -f hybrid_storage.log
```

### Option 2: Production with Uvicorn

```bash
# Install production server
pip install gunicorn

# Run with Uvicorn (recommended for async)
uvicorn main_hybrid:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4 \
    --log-level info
```

### Option 3: Docker Deployment

Create `Dockerfile`:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements_hybrid.txt .
RUN pip install --no-cache-dir -r requirements_hybrid.txt

# Copy application
COPY . .

# Create database directory
RUN mkdir -p /app/data

# Set environment
ENV PYTHONUNBUFFERED=1
ENV GOOGLE_APPLICATION_CREDENTIALS=/app/firebase-credentials.json

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/storage/health || exit 1

# Run application
CMD ["uvicorn", "main_hybrid:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:
```bash
# Build image
docker build -t coconut-detector:latest .

# Run container
docker run -d \
    -p 8000:8000 \
    -v $(pwd)/firebase-credentials.json:/app/firebase-credentials.json \
    -v $(pwd)/data:/app/data \
    --name coconut-detector \
    coconut-detector:latest

# View logs
docker logs -f coconut-detector

# Stop container
docker stop coconut-detector
```

### Option 4: Docker Compose

Create `docker-compose.yml`:
```yaml
version: '3.8'

services:
  api:
    build: .
    container_name: coconut-detector
    ports:
      - "8000:8000"
    volumes:
      - ./firebase-credentials.json:/app/firebase-credentials.json
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - PYTHONUNBUFFERED=1
      - GOOGLE_APPLICATION_CREDENTIALS=/app/firebase-credentials.json
      - LOG_LEVEL=INFO
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/storage/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  # Optional: PostgreSQL for advanced logging
  postgres:
    image: postgres:15-alpine
    container_name: coconut-db
    environment:
      - POSTGRES_USER=coconut
      - POSTGRES_PASSWORD=secure_password
      - POSTGRES_DB=coconut_detector
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

volumes:
  postgres_data:
```

Deploy:
```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f api

# Stop all services
docker-compose down
```

### Option 5: AWS/Cloud Deployment

#### AWS Lambda (Serverless):
```python
# Create handler for AWS Lambda
from mangum import Mangum
from main_hybrid import app

handler = Mangum(app)
```

Deploy:
```bash
pip install mangum zappa
zappa init  # Creates zappa_settings.json
zappa deploy production
```

#### Google Cloud Run:
```bash
# Requires Dockerfile
gcloud run deploy coconut-detector \
    --source . \
    --platform managed \
    --region us-central1 \
    --allow-unauthenticated \
    --set-env-vars GOOGLE_APPLICATION_CREDENTIALS=/app/firebase-credentials.json
```

#### Heroku:
```bash
# Create Procfile
echo "web: uvicorn main_hybrid:app --host 0.0.0.0 --port \$PORT" > Procfile

# Create runtime.txt
echo "python-3.11.7" > runtime.txt

# Deploy
heroku create coconut-detector
git push heroku main
```

---

## 📊 Production Configuration

### 1. Logging Setup

Add to `main_hybrid.py`:
```python
import logging
import logging.handlers

# Create logger
logger = logging.getLogger("coconut_detector")
logger.setLevel(logging.INFO)

# File handler (rotate every day, keep 7 days)
file_handler = logging.handlers.TimedRotatingFileHandler(
    'logs/app.log',
    when='midnight',
    interval=1,
    backupCount=7
)
file_handler.setFormatter(
    logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
)
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
)
logger.addHandler(console_handler)
```

### 2. Performance Optimization

```python
# main_hybrid.py

# 1. Increase sync interval in production
sync_manager = await get_sync_manager(
    sync_interval=300  # Check every 5 minutes instead of 60 seconds
)

# 2. Database optimization
storage = LocalStorageManager()
storage.clear_old_synced_records(days=30)  # Clean weekly

# 3. Connection pooling (if using external DB)
# Note: SQLite is file-based, but important for future migration
```

### 3. Security Hardening

```python
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],  # Specify domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add trusted host middleware
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["yourdomain.com", "www.yourdomain.com"]
)

# Add HTTPS redirect
@app.middleware("http")
async def https_redirect(request, call_next):
    if request.url.scheme == "http":
        url = request.url.replace(scheme="https")
        return RedirectResponse(url=url)
    return await call_next(request)
```

### 4. Rate Limiting

```python
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Apply to endpoints
@app.post("/detect/image")
@limiter.limit("10/minute")
async def detect_image(request: Request, ...):
    ...
```

### 5. Monitoring & Alerts

```python
# Setup Sentry for error tracking
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

sentry_sdk.init(
    dsn="https://your-sentry-dsn@sentry.io/xxx",
    integrations=[FastApiIntegration()],
    traces_sample_rate=1.0,
)

# Setup health check endpoint for uptime monitoring
@app.get("/health")
async def health_check():
    # Check all systems
    connectivity = check_internet_connectivity_sync()
    stats = local_storage.get_storage_stats()
    
    is_healthy = (
        connectivity["is_connected"] and
        sync_manager.is_running and
        stats["database_size_bytes"] < 5 * 1024 * 1024 * 1024  # 5GB limit
    )
    
    return {
        "status": "healthy" if is_healthy else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
    }
```

---

## 🔄 Backup & Recovery

### Automated Database Backup

```bash
#!/bin/bash
# backup.sh - Run daily via cron

BACKUP_DIR="/backups/coconut-detector"
DB_FILE="/app/hybrid_storage.db"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Backup database
cp $DB_FILE $BACKUP_DIR/hybrid_storage_$DATE.db

# Backup JSON records
tar -czf $BACKUP_DIR/records_$DATE.tar.gz /app/detection_records/

# Keep only last 30 days
find $BACKUP_DIR -mtime +30 -delete

echo "Backup completed: $DATE"
```

Schedule with cron:
```bash
# Run daily at 2 AM
0 2 * * * /path/to/backup.sh
```

### Recovery

```bash
# Restore database
cp /backups/hybrid_storage_20240420.db hybrid_storage.db

# Restore JSON records
tar -xzf /backups/records_20240420.tar.gz -C /
```

---

## 📊 Monitoring Dashboard (Grafana)

Create `monitoring.yml` for Prometheus:
```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'coconut-detector'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
```

Add to `main_hybrid.py`:
```python
from prometheus_client import Counter, Histogram, generate_latest

# Define metrics
detection_counter = Counter(
    'detections_total',
    'Total detections',
    ['user_id', 'status']
)

sync_duration = Histogram(
    'sync_duration_seconds',
    'Time to sync'
)

@app.get("/metrics")
async def metrics():
    return generate_latest()
```

---

## 🧪 Load Testing

```bash
# Install Apache Bench
apt-get install apache2-utils

# Simulate 1000 detections with 10 concurrent
ab -n 1000 -c 10 \
   -H "Authorization: Bearer TOKEN" \
   -p image.jpg \
   http://localhost:8000/detect/image

# Better: Use wrk for more realistic testing
wrk -t12 -c400 -d30s \
    -s script.lua \
    http://localhost:8000/detect/image
```

---

## 🔐 Security Checklist for Production

- [ ] HTTPS enabled (SSL certificate configured)
- [ ] Firewall rules restrict access (allow only needed ports)
- [ ] Firebase credentials secured in environment variable
- [ ] Database backups encrypted
- [ ] Rate limiting enabled
- [ ] CORS configured for trusted domains only
- [ ] Input validation enforced
- [ ] Logging configured (no sensitive data in logs)
- [ ] Error messages don't expose internals
- [ ] Database queries parameterized
- [ ] API keys rotated regularly
- [ ] Health monitoring alerts configured

---

## 📈 Scaling Considerations

For 10,000+ users:

1. **Database**: Consider PostgreSQL instead of SQLite
2. **Cache**: Add Redis for frequently accessed data
3. **Message Queue**: Use RabbitMQ for async sync operations
4. **Load Balancer**: Use NGINX or HAProxy
5. **Horizontal Scaling**: Run multiple API instances
6. **CDN**: Serve static files via CloudFront/Cloudflare

---

## 🆘 Common Deployment Issues

### Issue: "Permission denied" accessing database
```bash
# Fix permissions
chmod 644 hybrid_storage.db
chmod 755 $(dirname hybrid_storage.db)
```

### Issue: "Credentials not found" in Docker
```dockerfile
# Ensure credentials mounted
COPY firebase-credentials.json /app/
ENV GOOGLE_APPLICATION_CREDENTIALS=/app/firebase-credentials.json
```

### Issue: Out of memory
```python
# Reduce sync batch size
await firebase_sync.batch_sync_detections(
    detections,
    batch_size=50  # Reduce from 100
)
```

---

## ✅ Deployment Checklist

- [ ] Dependencies installed: `pip install -r requirements_hybrid.txt`
- [ ] Firebase credentials configured
- [ ] Environment variables set
- [ ] Database initialized
- [ ] Server starts without errors
- [ ] `/storage/health` returns healthy
- [ ] Test detection endpoint working
- [ ] Sync status showing correctly
- [ ] Logs configured and rotating
- [ ] Backups scheduled
- [ ] Monitoring alerts set up
- [ ] Rate limiting enabled
- [ ] HTTPS configured
- [ ] Security headers added

---

**Your hybrid storage system is production-ready!** 🚀
