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

**Drishya** (Sanskrit for *Vision/Observable*) is a full-spectrum OSINT platform designed for defense analysts, geopolitical researchers, and tactical operators. It synthesizes signals across 30+ countries and high-risk border sectors (including Siachen Glacier, Kargil, Nathu La, Sir Creek, and the LAC), coupling meteorological sensors, live world GIS overlays, automated multi-wire news aggregation, and local RAG-powered LLM intelligence synthesis.

---

## 🏗️ System Architecture

Drishya operates on a modern, decoupled client-server architecture built for resilience, rate-limit tolerance, and air-gapped fallback capability.

```mermaid
graph TD
  User[Browser Client / Analyst] -- 1. WebAuthn Cryptographic Handshake --> App[React 18 / Vite SPA]
  App -- 2. Real-time Telemetry & REST API --> FastAPI[FastAPI Ingestion Server]
  App -- 3. Live WebSocket Push --> WS[/ws WebSocket Stream]

  subgraph Ingestion & Processing Mesh
    FastAPI -- 4. Scheduled Parallel Query (Every 60s) --> IngestionMesh[Multi-Provider Ingestion Engine]
    IngestionMesh --> NewsAPIs[NewsAPI, GNews, Currents, Mediastack, TheNews, etc.]
    IngestionMesh --> WireRSS[Google News RSS / World Wires]
    NewsAPIs --> Classifier[Classifier & Deduplication Engine]
    WireRSS --> Classifier
    Classifier -- SHA-256 Deduplication & NLP Tagging --> DB[(PostgreSQL + pgvector / SQLite Fallback)]
    Classifier -- Broadcast Signal --> Redis[(Redis Pub/Sub)]
  end

  subgraph Intelligence Synthesis Deck
    App -- 5. Query / Upload Briefing Docs --> ChatEngine[AI Briefing & RAG Engine]
    ChatEngine -- Context Extraction (PDF/DOCX/TXT/Web) --> VectorSearch[TF-IDF & pgvector Similarity]
    VectorSearch --> LLM{Ollama: Llama 3.1 / Local Fallback}
    LLM -- Structured Strategic Dossier --> App
  end

  Redis --> WS
  WS --> App
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

### 3. 🧠 RAG-Powered AI Intelligence Summarizer & Chatbot
- Dynamic geopolitical summarizer that accepts external URL links and uploaded files (`.pdf`, `.docx`, `.txt`).
- Synthesizes internal database intelligence wires with operator uploads into standardized executive briefings.
- Native integration with **Ollama** (`llama3.1:8b-instruct`), with automatic fallback to deterministic offline synthesis when offline.

### 4. 📝 Ephemeral Collaborative Shared Notes Feed
- Live operator whiteboard for pinning tactical alerts, breaking news items, and custom field notes.
- Built-in 24-hour self-destruct countdown timers per note card.
- One-click **"Pin to Notes"** integration directly from live news telemetry cards and archive feeds.

### 5. 🛡️ Dual-Database & STRATCOM Security Gate
- Connects to **PostgreSQL with pgvector** for production vector embeddings.
- Automatic zero-configuration fallback to local SQLite (`articles_v2.db`) for offline/air-gapped operations.
- Biometric WebAuthn MFA simulation gate supporting role-based clearances (`analyst@intel.local`, `operator@intel.local`, `admin@intel.local`).

---

## 📂 Repository Structure

```text
OsINTDash/
├── backend/                  # FastAPI Application Mesh
│   ├── app/
│   │   ├── api/routes/       # Endpoints: alerts, archive, auth, chat, notes, summarizer, weather
│   │   ├── config.py         # App configuration & environment loader
│   │   ├── database.py       # Async SQLAlchemy engine (Postgres pgvector / SQLite)
│   │   ├── ingestion.py      # Multi-API parallel scraper & retry mesh
│   │   ├── classifier.py     # NLP classification & regex engine
│   │   ├── summarizer.py     # Document text parsing & LLM brief synthesizer
│   │   └── main.py           # FastAPI entrypoint, middleware, and WebSocket router
│   ├── requirements.txt      # Python dependencies (fastapi, uvicorn, websockets, sqlalchemy, etc.)
│   └── run.py                # Server launcher script
├── src/                      # React 18 + TypeScript Frontend
│   ├── components/           # UI components: Map, Hud, Dossier, Chat, Notes, Alerts
│   ├── services/             # API clients & WebSocket connection handlers
│   ├── App.tsx               # Main application layout and view router
│   ├── main.tsx              # React mounting root
│   └── styles.css            # Tactical HUD styling, animations, and scanline shaders
├── data/                     # Local SQLite database fallback storage
├── Dockerfile.backend        # Unified backend/frontend Docker container
├── Dockerfile.frontend       # Standalone frontend Nginx container
├── docker-compose.yml        # Multi-service stack (Backend, Frontend, Postgres, Redis)
├── launch-drishya.cmd        # Windows 1-click execution script
├── launch-drishya.ps1        # PowerShell automated launcher & Ollama checker
├── package.json              # Project scripts and frontend dependencies
├── render.yaml               # Render.com Blueprint deployment specification
└── vite.config.ts            # Vite bundler and API/WS proxy configuration
```

---

## 🚀 Getting Started

### Prerequisites
- **Node.js** (v18.0.0 or higher)
- **Python** (v3.11 or higher)
- **npm** (v9.0.0 or higher)
- *(Optional)* **Ollama** for local AI synthesis ([ollama.com](https://ollama.com))

---

### Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/CaptDoom/OsINTDash.git
   cd OsINTDash
   ```

2. **Install frontend dependencies:**
   ```bash
   npm install
   ```

3. **Set up Python virtual environment and backend dependencies:**
   ```bash
   # Windows (PowerShell)
   python -m venv venv
   .\venv\Scripts\pip install -r backend/requirements.txt

   # Linux / macOS
   python3 -m venv venv
   ./venv/bin/pip install -r backend/requirements.txt
   ```

4. **Configure Environment Variables:**
   Copy `.env.example` to `.env` in the project root:
   ```bash
   cp .env.example .env
   ```
   Add your API keys to `.env`:
   ```env
   NEWS_API_KEY=your_newsapi_key
   NEWSDATA_API_KEY=your_newsdata_key
   GNEWS_API_KEY=your_gnews_key
   CURRENTS_API_KEY=your_currents_key
   OPENWEATHERMAP_API_KEY=your_openweather_key
   LLM_PROVIDER=ollama
   LLM_MODEL=llama3.1:8b-instruct
   OLLAMA_BASE_URL=http://127.0.0.1:11434
   ```

---

### Running the Application

#### Option A: Quick Launch (Windows)
Double-click `launch-drishya.cmd` or run:
```powershell
.\launch-drishya.ps1
```
This automatically verifies dependencies, starts Ollama if installed, boots both services, and launches the browser to [http://localhost:3000](http://localhost:3000).

#### Option B: Standard Development Run
Run both frontend and backend concurrently:
```bash
npm run dev
```

* **Frontend Dashboard:** [http://localhost:3000](http://localhost:3000)
* **Backend API:** [http://localhost:3001](http://localhost:3001)
* **Interactive API Docs (Swagger):** [http://localhost:3001/docs](http://localhost:3001/docs)

---

## 🧪 Testing

To execute the automated backend test suite:
```bash
# Using pytest
python -m pytest -q backend/tests/test_all.py

# Using standard unittest
python -m unittest backend/tests/test_all.py
```

---

## 🐳 Docker Deployment

To launch the full containerized stack (PostgreSQL with pgvector, Redis, FastAPI Backend, and Nginx Frontend):

```bash
docker-compose up --build -d
```

Access the dashboard at `http://localhost:3000`.

---

## ☁️ Cloud Deployment (Render)

This repository includes a native [`render.yaml`](render.yaml) blueprint:
1. Connect your GitHub repository to [Render.com](https://render.com).
2. Deploy using the **Web Service** configured with `Dockerfile.backend`.
3. Set your environment variables in the Render dashboard.
4. The service will automatically build frontend assets and serve both API and SPA through Uvicorn.

---

## 📖 Operational Documentation

- **[USER_GUIDE.md](USER_GUIDE.md)**: Operator manual for navigating the tactical map, uploading intelligence files, and collaborating on the shared whiteboard.
- **[README_DEPLOYMENT.md](README_DEPLOYMENT.md)**: Production deployment guide for Docker, Nginx, and cloud hosts.
- **[architecture_and_report.md](architecture_and_report.md)**: In-depth technical specification and classification heuristics.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
