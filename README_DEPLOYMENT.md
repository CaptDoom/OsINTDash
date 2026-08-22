# Deployment

This repository can be deployed locally using Docker Compose.

## Local deployment

1. Build and start all services:

```bash
docker-compose up --build
```

Set provider credentials in `.env` before starting Compose. Never commit `.env` or
place real API keys in `.env.example`. The backend health check is available at
`http://localhost:3001/health`; readiness also verifies Redis and may report
`503 degraded` when Redis is unavailable.

2. Access:
- Frontend: http://localhost:3000
- Backend API: http://localhost:3001


2. Access:
- Frontend: http://localhost:3000
- Backend API: http://localhost:3001

## Service ports
- `frontend`: 3000
- `backend`: 3001
- `postgres`: 5432
- `redis`: 6379

## Render deployment

This repository is configured for one-click Render Blueprint deployments using the `render.yaml` configuration file.

1. Connect your repository to Render.
2. Select **Blueprints** from the Render Dashboard and click **New Blueprint Instance**.
3. Render will automatically discover `render.yaml` and spin up:
   - **Web Service (`drishya-web`)**: Serves the API and static frontend assets using `Dockerfile.backend`.
   - **PostgreSQL Database (`drishya-db`)**: Managed database resource (fully compatible with `pgvector`).
   - **Redis Cache (`drishya-redis`)**: High-performance key-value store.
4. The database and Redis connection string variables (`DATABASE_URL` and `REDIS_URL`) will be automatically generated and linked.
5. If desired, you can add any optional API keys (e.g. `NEWSAPI_KEY`, `OPENAI_API_KEY`) under the Environment Variables section of `drishya-web` on Render.

### Render notes
- The backend container builds the frontend assets during the Docker build stage and serves them directly from FastAPI.
- On startup, the backend automatically performs database schema checks and seeds over 4,800+ realistic geopolitical articles into the PostgreSQL database.

## Notes
- The frontend is served by Nginx locally in Docker Compose and proxies `/api` to the backend service.
- Set any optional API keys in the environment before starting Docker Compose.
