# Drishya (OsINTDash) 🛰️

**An advanced, real-time Open-Source Tactical Intelligence (OSINT) and geopolitical telemetry dashboard monitoring border sectors, strategic allies, and global superpowers.**

[![Repository](https://img.shields.io/badge/GitHub-CaptDoom%2FOsINTDash-181717?style=flat&logo=github)](https://github.com/CaptDoom/OsINTDash)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18.3+-61DAFB?style=flat&logo=react&logoColor=black)](https://reactjs.org/)
[![Vite](https://img.shields.io/badge/Vite-5.4+-646CFF?style=flat&logo=vite&logoColor=white)](https://vitejs.dev/)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-3.4+-38B2AC?style=flat&logo=tailwind-css&logoColor=white)](https://tailwindcss.com/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## 📌 Executive Overview

**Drishya** (Sanskrit for *Vision/Observable*) is a full-spectrum OSINT platform designed for defense analysts, geopolitical researchers, and tactical operators. It synthesizes signals across 30+ countries and high-risk border sectors (including Siachen Glacier, Kargil, Nathu La, Sir Creek, and the LAC), coupling meteorological sensors, live world GIS overlays, automated multi-wire news aggregation, GDELT geopolitical event ingestion, and local RAG-powered LLM intelligence synthesis.

### What's New (v2.2)

- **🌍 GDELT 2.0 Ingestion Worker** — Polls the GDELT Events dataset every 5 minutes, filters to target countries (FIPS→ISO mapping), and inserts geopolitical events into the database with 3-layer deduplication.
- **⚡ Realtime Architecture** — WebSocket broadcasts after every DB insert, SSE endpoint for lightweight clients, 2-minute news ingestion cycles.
- **📊 Production Metrics** — GDELT-specific Prometheus counters on `/metrics` (cycles, fetched, filtered, deduped, inserted, errors).
- **🛡️ Redis Hardening** — 30-second cooldown after connection failure prevents connection storms.
- **🔗 Source Links** — Country detail slideout now displays multi-source corroboration links.

---

## 🏗️ System Architecture

Drishya operates on a modern, decoupled client-server architecture built for resilience, rate-limit tolerance, and air-gapped fallback capability.

```mermaid
graph TD
  User[Browser Client / Analyst] -- 1. WebAuthn Cryptographic Handshake --> App[React 18 / Vite SPA]
  App -- 2. Real-time Telemetry & REST API --> FastAPI[FastAPI Ingestion Server]
  App -- 3. Live WebSocket Push --> WS[/ws WebSocket Stream]
  App -- 3b. SSE Stream --> SSE[/api/events/stream]

  subgraph Ingestion & Processing Mesh
    FastAPI -- 4. Scheduled Parallel Query (Every 2 min) --> IngestionMesh[Multi-Provider Ingestion Engine]
    IngestionMesh --> NewsAPIs[NewsAPI, GNews, Currents, Mediastack, TheNews, etc.]
    IngestionMesh --> WireRSS[Google News RSS / World Wires]
    FastAPI -- 5. GDELT Poll (Every 5 min) --> GDELT[GDELT 2.0 Events Worker]
    GDELT --> GDZIP[events.export.CSV.zip]
    NewsAPIs --> Classifier[Classifier & Deduplication Engine]
    WireRSS --> Classifier
    Classifier -- SHA-256 Dedup & NLP Tagging --> DB[(PostgreSQL + pgvector / SQLite Fallback)]
    Classifier -- Broadcast Signal --> Redis[(Redis Pub/Sub)]
    GDELT -- Direct Insert + Broadcast --> DB
    GDELT -- WebSocket Broadcast --> Redis
  end

  subgraph Intelligence Synthesis Deck
    App -- 6. Query / Upload Briefing Docs --> ChatEngine[AI Briefing & RAG Engine]
    ChatEngine -- Context Extraction (PDF/DOCX/TXT/Web) --> VectorSearch[TF-IDF & pgvector Similarity]
    VectorSearch --> LLM{Ollama: Llama 3.1 / Local Fallback}
    LLM -- Structured Strategic Dossier --> App
  end

  Redis --> WS
  Redis --> SSE
  WS --> App
  SSE --> App
```

---

## 🌟 Key Capabilities & Features

### 1. 🗺️ D3 Geospatial Tactical GIS World Map
- Interactive projected coordinate graticule grid lines and pulsating threat beacons.
- Dynamic sector selection for 30+ monitored countries and critical geopolitical nodes (India, China, Pakistan, Taiwan, US, Russia, Iran, Israel, etc.).
- Active meteorological HUD station overlays displaying localized real-time conditions, wind speeds, and barometric indicators.

### 2. ⚡ Multi-Source Resilient News Ingestion Mesh
- Unified high-density queries scraping up to **500 raw articles** per sweep cycle.
- Integrates 10+ news providers with automatic backoff, jitter, and circuit breakers:
  - **NewsAPI.org**, **World News API**, **NewsData**, **GNews**, **Currents API**, **TheNewsAPI**, **Mediastack**, **Newscatcher**, **Bing News**, and **Google News RSS**.
- Word-boundary regex classification engine categorizing signals across **Military & Defense**, **Political & Diplomatic**, **Economic & Financial**, **Technology & Cyber**, and **Social & Civil**.
- **Realtime**: New articles are broadcast via WebSocket immediately after DB insert (2-minute cycle).

### 3. 🌍 GDELT 2.0 Geopolitical Event Ingestion
- Polls the GDELT Events dataset every **5 minutes** for new geopolitical events.
- **FIPS→ISO country code mapping** for 30+ target countries.
- **3-layer deduplication**: intra-batch URL dedup, DB GLOBALEVENTID dedup, DB URL dedup.
- **Dialect-agnostic insert**: SQLite `OR IGNORE` / Postgres `ON CONFLICT DO NOTHING`.
- **Realtime broadcast**: High/medium impact events pushed to WebSocket immediately after insert.
- **Production metrics**: Prometheus counters for cycles, fetched, filtered, deduped, inserted, errors.
- **Manual trigger**: `POST /api/gdelt/ingest` for on-demand ingestion.
- **Exponential backoff**: 30s → 60s → 120s → 300s cap on consecutive failures.

### 4. 🧠 RAG-Powered AI Intelligence Summarizer & Chatbot
- Dynamic geopolitical summarizer that accepts external URL links and uploaded files (`.pdf`, `.docx`, `.txt`).
- Synthesizes internal database intelligence wires with operator uploads into standardized executive briefings.
- Native integration with **Ollama** (`llama3.1:8b-instruct`), with automatic fallback to deterministic offline synthesis when offline.

### 5. 📝 Ephemeral Collaborative Shared Notes Feed
- Live operator whiteboard for pinning tactical alerts, breaking news items, and custom field notes.
- Built-in 24-hour self-destruct countdown timers per note card.
- One-click **"Pin to Notes"** integration directly from live news telemetry cards and archive feeds.

### 6. 📡 Realtime Data Delivery
- **WebSocket** (`/ws`): Channel-based pub/sub for alerts, weather, notes, map updates.
- **SSE** (`/api/events/stream`): Server-Sent Events for lightweight clients with 30s heartbeat keepalive.
- **Redis Pub/Sub**: Cross-instance event broadcasting for horizontal scaling.
- **In-Memory Stream**: Fallback when Redis is offline.

### 7. 🛡️ Dual-Database & STRATCOM Security Gate
- Connects to **PostgreSQL with pgvector** for production vector embeddings.
- Automatic zero-configuration fallback to local SQLite (`articles_v2.db`) for offline/air-gapped operations.
- Biometric WebAuthn MFA simulation gate supporting role-based clearances (`analyst@intel.local`, `operator@intel.local`, `admin@intel.local`).

---

## 📂 Repository Structure

```text
OsINTDash/
├── backend/                      # FastAPI Application Mesh
│   ├── app/
│   │   ├── api/routes/           # Endpoints: alerts, archive, auth, chat, notes, summarizer, weather
│   │   ├── config.py             # App configuration & environment loader
│   │   ├── database.py           # Async SQLAlchemy engine (Postgres pgvector / SQLite)
│   │   ├── main.py               # FastAPI entrypoint, middleware, WebSocket router, SSE endpoint
│   │   ├── observability.py      # Prometheus metrics registry
│   │   ├── redis_pool.py         # Shared Redis connection pool with cooldown
│   │   ├── settings.py           # Pydantic BaseSettings with env loading
│   │   └── services/
│   │       ├── classifier.py     # NLP classification, dedup, & memory live stream
│   │       ├── credibility.py    # Cross-corroboration & source reputation
│   │       ├── circuit_breaker.py# Health monitoring & circuit breaker
│   │       ├── gdelt_worker.py   # GDELT 2.0 Events ingestion worker
│   │       ├── ingestion.py      # Multi-API parallel scraper & retry mesh
│   │       ├── job_store.py      # Background job management
│   │       ├── rate_limiter.py   # Request rate limiting middleware
│   │       ├── risk.py           # Country risk scoring engine
│   │       └── summarizer.py     # Document text parsing & LLM brief synthesizer
│   ├── tests/
│   │   └── test_all.py           # Automated backend test suite (58 tests)
│   ├── requirements.txt          # Python dependencies
│   └── run.py                    # Server launcher script
├── src/                          # React 18 + TypeScript Frontend
│   ├── components/               # UI: Map, Hud, Dossier, Chat, Notes, Alerts, CountryDetail
│   ├── App.tsx                   # Main application layout and view router
│   ├── main.tsx                  # React mounting root
│   └── styles.css                # Tactical HUD styling, animations, and scanline shaders
├── data/                         # Local SQLite database fallback storage
├── Dockerfile.backend            # Unified backend/frontend Docker container
├── Dockerfile.frontend           # Standalone frontend Nginx container
├── docker-compose.yml            # Multi-service stack (Backend, Frontend, Postgres, Redis)
├── launch-drishya.cmd            # Windows 1-click execution script
├── launch-drishya.ps1            # PowerShell automated launcher & Ollama checker
├── package.json                  # Project scripts and frontend dependencies
├── render.yaml                   # Render.com Blueprint deployment specification
└── vite.config.ts                # Vite bundler and API/WS proxy configuration
```

---

## 🚀 Getting Started

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| **Node.js** | v18.0.0+ | [Download](https://nodejs.org/) |
| **Python** | v3.11+ | [Download](https://python.org/) |
| **npm** | v9.0.0+ | Bundled with Node.js |
| **Redis** | v7.0+ | Optional — auto-fallback to in-memory stream |
| **PostgreSQL** | v14+ | Optional — auto-fallback to SQLite |
| **Ollama** | Latest | Optional — for local AI synthesis |

### Installation

#### 1. Clone the repository

```bash
git clone https://github.com/CaptDoom/OsINTDash.git
cd OsINTDash
```

#### 2. Install frontend dependencies

```bash
npm install
```

#### 3. Set up Python backend

```bash
# Create virtual environment
python -m venv venv

# Activate it
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# Windows (Git Bash):
source venv/Scripts/activate
# Linux / macOS:
source venv/bin/activate

# Install dependencies
pip install -r backend/requirements.txt
```

#### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your API keys (at least one news provider is recommended):

```env
# Database (auto-fallback to SQLite if not set)
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/drishya_db

# Redis (auto-fallback to in-memory if not set)
REDIS_URL=redis://localhost:6379/0

# News Providers (at least one recommended)
NEWS_API_KEY=your_newsapi_key
NEWSDATA_API_KEY=your_newsdata_key
GNEWS_API_KEY=your_gnews_key

# Weather
OPENWEATHERMAP_API_KEY=your_openweather_key

# AI Synthesis (optional)
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b-instruct
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

> **Note:** No API keys required for basic operation. Drishya falls back to free GDELT/RSS feeds when commercial providers are unavailable.

### Running the Application

#### Option A: Quick Launch (Windows)

Double-click `launch-drishya.cmd` or run:

```powershell
.\launch-drishya.ps1
```

This automatically verifies dependencies, starts Ollama if installed, boots both services, and launches the browser.

#### Option B: Standard Development

```bash
npm run dev
```

| Service | URL |
|---|---|
| **Frontend Dashboard** | http://localhost:3000 |
| **Backend API** | http://localhost:3001 |
| **Swagger Docs** | http://localhost:3001/docs |
| **Prometheus Metrics** | http://localhost:3001/metrics |

---

## 📡 API Endpoints

### Core Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/ready` | Readiness probe (DB + Redis) |
| `GET` | `/metrics` | Prometheus metrics (including GDELT counters) |
| `GET` | `/api/news/all` | All news signals by country |
| `GET` | `/api/news/country` | Country-specific news |
| `POST` | `/api/news/refresh` | Trigger manual news ingestion |
| `GET` | `/api/risk/country` | Country risk score |

### Realtime Endpoints

| Method | Path | Description |
|---|---|---|
| `WS` | `/ws` | WebSocket — channel-based alerts, weather, notes, map |
| `GET` | `/api/events/stream` | SSE — Server-Sent Events for lightweight clients |

### GDELT Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/gdelt/ingest` | Manually trigger a GDELT ingestion cycle |

### Intelligence Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat` | AI briefing chat |
| `POST` | `/api/summarize` | Document summarization |
| `GET` | `/api/notes` | Shared notes |
| `GET` | `/api/alerts/active` | Active alert rules |
| `GET` | `/api/weather/border` | Border sector weather |

---

## 📊 Realtime Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     REALTIME PIPELINES                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  GDELT Pipeline (every 5 min):                                  │
│    ZIP download → parse 61-col CSV → filter by FIPS codes       │
│    → 3-layer dedup → DB insert → WebSocket broadcast            │
│    → Prometheus metrics update                                   │
│                                                                 │
│  News Pipeline (every 2 min):                                   │
│    10+ API providers → classify & score → dedup                 │
│    → DB insert → Redis pubsub → WebSocket broadcast             │
│                                                                 │
│  Client Delivery:                                               │
│    WebSocket (/ws) → channel-based alerts, weather, notes       │
│    SSE (/api/events/stream) → lightweight EventSource stream    │
│    REST (/api/news/all) → on-demand country queries             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🧪 Testing

```bash
# Run all backend tests (58 tests)
cd backend
python -m pytest tests/test_all.py -q

# Run specific test class
python -m pytest tests/test_all.py::TestClassifier -v

# Run with coverage
python -m pytest tests/test_all.py --cov=app
```

---

## 🐳 Docker Deployment

```bash
# Build and start all services
docker-compose up --build -d

# View logs
docker-compose logs -f backend

# Stop
docker-compose down
```

Services: Backend (FastAPI), Frontend (Nginx), PostgreSQL (pgvector), Redis.

---

## ☁️ Cloud Deployment (Render)

This repository includes a native [`render.yaml`](render.yaml) blueprint:

1. Connect your GitHub repository to [Render.com](https://render.com).
2. Deploy using the **Web Service** configured with `Dockerfile.backend`.
3. Set environment variables in the Render dashboard.
4. The service will automatically build frontend assets and serve both API and SPA through Uvicorn.

---

## 📖 Operational Documentation

- **[USER_GUIDE.md](USER_GUIDE.md)**: Operator manual for navigating the tactical map, uploading intelligence files, and collaborating on the shared whiteboard.
- **[README_DEPLOYMENT.md](README_DEPLOYMENT.md)**: Production deployment guide for Docker, Nginx, and cloud hosts.
- **[architecture_and_report.md](architecture_and_report.md)**: In-depth technical specification and classification heuristics.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
