# Deployment

This repository can be deployed locally using Docker Compose.

## Local deployment

1. Build and start all services:

```bash
docker-compose up --build
```

2. Access:
- Frontend: http://localhost:3000
- Backend API: http://localhost:3001

## Service ports
- `frontend`: 3000
- `backend`: 3001
- `postgres`: 5432
- `redis`: 6379

## Render deployment

This repo is now Render-ready via `render.yaml` and a single web service container.

1. Create a new Render Web Service and connect your repository branch.
2. Use `Docker` environment and `Dockerfile.backend` as the build image.
3. Set these environment variables on Render:
   - `DATABASE_URL` (managed Postgres, e.g. `postgresql+asyncpg://user:pass@host:5432/db`)
   - `REDIS_URL` (managed Redis or Redis add-on, e.g. `redis://host:6379/0`)
   - `APP_ENV=production`
   - Any optional API keys: `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `NEWSAPI_KEY`, etc.
4. Set the start command to:

```bash
sh -c "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-3001}"
```

### Render notes
- The backend container builds the frontend assets during Docker build and serves them from FastAPI.
- Render should use a managed Postgres database and a Redis service or add-on rather than local Docker Compose for production.

## Notes
- The frontend is served by Nginx locally in Docker Compose and proxies `/api` to the backend service.
- Set any optional API keys in the environment before starting Docker Compose.
