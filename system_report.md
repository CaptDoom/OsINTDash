# Operational System Report: Drishya 2.0

This report provides a comprehensive breakdown of the architecture, design, components, working processes, and dependencies of **Drishya 2.0**—a high-performance, real-time tactical telemetry and geopolitical news intelligence dashboard monitoring India's border sectors.

---

## 1. System Architecture Overview

Drishya 2.0 is designed as a decoupled, modern multi-tier web application. It combines a high-performance React/TypeScript Single Page Application (SPA) frontend with a Python FastAPI ingestion mesh, backed by a dual-database design (PostgreSQL/SQLite) and a Redis-backed message broker.

```mermaid
graph TD
  User[Browser Client] -- 1. Authenticates via WebAuthn MFA --> App[React SPA: App.tsx]
  App -- 2. Polls /api/news/all & establishes WebSocket/SSE --> FastAPI[FastAPI Server: main.py]
  
  subgraph Ingestion & Processing Mesh
    FastAPI -- 3. Periodic trigger (every 60s) --> Ingestion[Ingestion Engine: ingestion.py]
    Ingestion -- 4. Queries 10 News APIs in parallel --> NewsAPIs[NewsAPI, GNews, Currents, Bing, etc.]
    Ingestion -- 5. Fallback RSS Scraping --> GoogleRSS[Google News RSS Feed]
    
    NewsAPIs --> Classifier[Classifier: classifier.py]
    GoogleRSS --> Classifier
    
    Classifier -- 6. SHA-256 Deduplication & Regex Tagging --> DB[(SQLite / PostgreSQL DB)]
    Classifier -- 7. Publishes live updates --> Redis[(Redis Pub/Sub & Lock Store)]
  end
  
  subgraph Geopolitical Intelligence Chatbot (RAG)
    App -- 8. Prompts /api/chat/query or uploads documents --> ChatRouter[Chat Route: routes/chat.py]
    ChatRouter -- 9. Text Extraction (Docling / PyPDF) --> SimpleText[Document Text]
    SimpleText -- 10. TF-IDF & Cosine Similarity search --> DB
    SimpleText & DB -- 11. LLM Prompt Context --> LLM{Ollama / OpenAI / Gemini / Local Fallback}
    LLM -- 12. Synthesized AI Briefing + Cited Links --> App
  end
  
  Redis -- 13. Event broadcasting --> WebSocket[WebSocket endpoint: /ws]
  WebSocket -- 14. Pushes live alerts --> App
```

### Core Architecture Characteristics:
*   **Decoupled Frontend & Backend**: The UI is built using Vite/React and communicates with the FastAPI backend strictly via REST APIs, Server-Sent Events (SSE), and WebSockets.
*   **Mesh API Fallback & Ingestion**: The system pulls from up to 10 independent wire/news API providers, automatically falling back to scraping Google News RSS feeds if credentials or quota run out.
*   **Local LLM Integration (Ollama)**: Enables running offline LLM synthesis (Briefings, RAG chatbot) using local weights (`llama3.1:8b-instruct`), falling back to rule-based template generation if the LLM backend is offline.
*   **Dual-Database Hybrid Mode**: Natively targets PostgreSQL (with `pgvector` for similarity calculations) but implements a full SQLite local database fallback (`aiosqlite` + `JSONVector` type handler) for zero-config local runs.

---

## 2. Frontend Layer (React + TypeScript + Vite)

The frontend is a single-page application built on React, TypeScript, and Vite. It is optimized for sub-second hot reloading, high density, and dark-mode aesthetic.

### A. Core File Hierarchy
*   [`src/main.tsx`](file:///c:/Users/Asus/Desktop/DashNews/src/main.tsx): Mounts the React virtual DOM to the `#root` element of the page.
*   [`src/App.tsx`](file:///c:/Users/Asus/Desktop/DashNews/src/App.tsx): Contains the main state management, WebAuthn MFA login gates, Dossier Grid View panels, settings panels, UAV/stability gauges, and Chatbot HUD.
*   [`src/components/WorldGeoMap.tsx`](file:///c:/Users/Asus/Desktop/DashNews/src/components/WorldGeoMap.tsx): An interactive SVG map powered by D3-Geo, plotting national borders and highlighting geopolitical alerts.
*   [`src/styles.css`](file:///c:/Users/Asus/Desktop/DashNews/src/styles.css): Houses the CSS animations, scanline grids, terminal font loads, hover glow filters, marquee tickers, and custom Tailwind parameters.

### B. User Flow & Key UI Modules

#### 1. Security Access Gate (WebAuthn MFA Simulation)
*   Access requires a verified analyst email (`analyst@intel.local`, `operator@intel.local`, or `admin@intel.local`).
*   Instead of traditional passwords, authentication simulates a WebAuthn biometric cryptographic handshake. When the user interacts with the fingerprint icon, a simulated credential verification is executed, generating secure session parameters.

#### 2. Sector Dossiers Grid Landing View
The dashboard divides geopolitical monitoring into nine borders surrounding India:
*   **Critical Sectors**: China, Pakistan, Afghanistan, Myanmar.
*   **Moderate/High Sectors**: Bangladesh, Nepal, Bhutan, Sri Lanka, Maldives.

Each country card displays:
*   **Thematic News Cards**: 5 columns rendering classified news streams: `Political`, `Social`, `Tech`, `Economic`, and `Military`.
*   **Interactive Telemetry Gauges**: Drone/UAV output levels, Border Stability Indices, and Risk Probability meters.
*   **Last Synced Telemetry**: Linear depleted countdown timers tracking the 60-second polling cycle.

#### 3. D3 World Geo Map Panel
*   Renders interactive geographical vectors utilizing [`world-atlas/countries-110m.json`](file:///c:/Users/Asus/Desktop/DashNews/node_modules/world-atlas/countries-110m.json) and coordinates from `world-countries`.
*   Highlights monitored border states with glowing overlays and anchors pulsing beacon markers for high-impact alerts, linking directly to verification pages.

#### 4. OSINT Tactical Intelligence Chatbot & Document Fuser
*   **Chat Panel**: A bottom-anchored HUD allowing real-time OSINT queries regarding troop movements, border development, and economic activity.
*   **Fuser UI**: An interactive drop-zone supporting PDF, DOCX, and TXT document uploads. Fuses the uploaded file contents with real-time news archives using RAG, returning an executive citation briefing.

---

## 3. Backend Layer (Python FastAPI)

The backend is built using FastAPI. It drives the data ingestion pipeline, manages the SQLite/Postgres datastores, handles LLM queries, and serves real-time pub/sub streams.

### A. Database Layer (`backend/app/database.py`)
*   **Declarative Models**: Defined via SQLAlchemy.
    *   `Article`: Represents an individual intelligence news item. Fields include `id`, `title`, `headline`, `summary`, `content`, `url`, `source`, `country_code`, `published_at`, `impact_level`, `department`, `embedding`, and `created_at`.
    *   `ArchiveSummary`: Caches generated LLM summaries for `1M`, `6M`, and `1Y` historical intervals.
*   **Dual Engine Fallback**: During startup (`init_db_engine()`), the backend checks if a PostgreSQL server is reachable. If it fails, it seamlessly falls back to a local SQLite database (`articles_v2.db` saved inside the `data/` folder).
*   **Vector Datatypes**: Natively integrates `pgvector` for Postgres databases. For the SQLite fallback, it maps a custom `JSONVector` type decorator that marshals vector listings into text blobs.
*   **Failsafe Seeding**: If the database has fewer than 350 articles, a seeding generator populates the datastore with randomized but realistic high-density border security alerts.

### B. Ingestion Mesh Engine (`backend/app/services/ingestion.py`)
*   **API Orchestration**: Dynamically checks for credentials to query 10 news APIs: `NewsAPI`, `GNews`, `NewsData`, `WorldNewsAPI`, `Finnhub`, `Currents`, `TheNews`, `Mediastack`, `Newscatcher`, and `Bing News`.
*   **Parallel Scraping**: Uses `asyncio.Semaphore(request_concurrency)` to limit concurrent HTTP connections to prevent client-side rate limits.
*   **Universal RSS Fallback**: If standard APIs fail or lack credentials, the pipeline runs parallel search queries on the Google News RSS feed, parsing the XML payloads manually via standard libraries.
*   **Circuit Breaker Protection**: Implements a custom `CircuitState` mechanism. If an external service returns three consecutive timeouts, rate limits (HTTP 429), or server errors (HTTP 5xx), the circuit opens, bypassing that provider for a configured cooldown period.

### C. Filtering & Classification Engine (`backend/app/services/classifier.py`)
To keep OSINT feeds clear of noise, every ingested article goes through a three-stage filter:
1.  **Deduplication**: Computes a SHA-256 fingerprint from the article URL and the first 280 characters of the content. This fingerprint is checked against a Redis cache (with a 24-hour expiration) or a local memory register.
2.  **Noise Filter**: Filters out clickbait, sports quizzes, weather, and celebrity gossip via keyword match configurations.
3.  **Classification Tagging**: Scans text via regular expressions utilizing strict word boundaries (`\b...s?\b`) to prevent overlapping matches. Assigns categories:
    *   *Departments*: `Military & Defense`, `Economic & Financial`, `Social Affairs & Welfare`, or `Political & Diplomatic`. (Keywords matching tech categories like AI, UAV, cybersecurity, and space upgrade the department tag to `Tech` on the frontend).
    *   *Department Severity*: `High Impact`, `Medium Impact`, or `Normal Impact`.

### D. LLM Synthesis & Summarizer (`backend/app/services/summarizer.py`)
*   **Mesh API Execution**: Can route queries to `Ollama` (`llama3.1:8b-instruct` at `http://127.0.0.1:11434`), `OpenAI` (`gpt-4o-mini`), or `Gemini` (`gemini-2.5-flash`).
*   **Token-Aware Ingestion**: Computes a token estimate using a character division heuristic (`len(text) // 4`). If the tokens fit within the maximum API window, it executes a single-pass summary. If the context exceeds the threshold, it triggers a recursive Map-Reduce pipeline.
*   **Offline Fallback Heuristic**: If no LLM is configured or APIs are unreachable, a rule-based generator sorts articles by department and outputs a structured Markdown executive summary.

### E. Background Job Store (`backend/app/services/job_store.py`)
*   Handles long-running tasks such as document uploads (fusion RAG) and real-time scrapers.
*   Implements a task tracker utilizing Redis HSETs and Pub/Sub. When Redis is unavailable, it automatically falls back to an in-memory queueing store using `asyncio.Queue`.
*   Job states transition: `queued` ➔ `parsing` ➔ `searching` ➔ `synthesizing` ➔ `completed`/`failed`.

### F. Chat & RAG Endpoint (`backend/app/api/routes/chat.py`)
*   **Document Upload & Extraction**: Handles PDF, DOCX, and TXT files. It prioritizes IBM's `Docling` library for high-fidelity markdown export. If Docling is missing, it falls back to simple text parsers (`pypdf`, `docx`).
*   **TF-IDF Similarity Search**: Runs text overlap and vector-based scoring across the database, boosting high-impact articles.
*   **RAG Fusion Prompt**: Feeds the top 5 relevant articles along with the query or uploaded document into the configured LLM, returning a citation briefing.

---

## 4. Key Dependencies & Libraries

### Frontend Package Stack (`package.json`)
*   `react` & `react-dom` (v18.3.1): UI rendering tree.
*   `vite` (v5.4.10): Dev server and build bundling.
*   `d3-geo` (v3.1.1): SVG geographical projection math.
*   `topojson-client` (v3.1.0): TopoJSON parsing for D3 coordinates.
*   `world-atlas` (v2.0.2): Geographic vector data for mapping.
*   `world-countries` (v5.1.0): Country capital and metadata dataset.
*   `ws` (v8.18.0): Client-side WebSocket communication.

### Backend Package Stack (`backend/requirements.txt`)
*   `fastapi` (v0.110.0) & `uvicorn` (v0.28.0): API routing and Web Server.
*   `sqlalchemy` (v2.0.28) & `aiosqlite` (v0.20.0): Async SQL ORM and SQLite backend database.
*   `asyncpg` (v0.29.0) & `pgvector` (v0.2.5): PostgreSQL connectivity and vector storage.
*   `redis` (v5.0.3): Pub/Sub messaging and dedup caching.
*   `pydantic-settings` (v2.2.1): Pydantic environment configuration loader.
*   `httpx` (v0.27.0): Concurrent async network requests.
*   `pypdf` (v4.1.0) & `python-docx` (v1.1.0): Backup text extraction libraries.

---

## 5. System Execution Flows

### A. Data Ingestion & Live Broadcast Flow
```text
[Uvicorn Periodic Ingest]
          │
          ▼
   Fetch raw news
   (10 APIs / Google RSS)
          │
          ▼
   SHA-256 Deduplication
   (Cached via Redis)
          │
          ▼
   Classification & Regex Tagging
   (Department / Impact / Tech boost)
          │
          ├──► Save to DB (PostgreSQL / SQLite)
          │
          ▼
   Broadcast Live Signal
   (Redis live_stream channel)
          │
          ▼
   Websocket API Endpoints
          │
          ▼
   Browser clients update UI (News ticker, glowing indicator)
```

### B. Document Fusion & Chat RAG Flow
```text
[User query or Document Upload]
                │
                ▼
      Extract Text (Docling/PDF)
                │
                ▼
      TF-IDF/Cosine Similarity Search
      (Across High Impact articles)
                │
                ▼
      Build Prompt Context
      (Extracted text + Top 5 source references)
                │
                ▼
      LLM Synthesis (Ollama / OpenAI / Gemini)
                │
                ▼
      Return Briefing + Hyperlink Citations to UI
```

---

## 6. Docker & Deployment Configurations

The project contains native configuration files for reproducible production deployments:

*   [`docker-compose.yml`](file:///c:/Users/Asus/Desktop/DashNews/docker-compose.yml): Coordinates 4 core services for local orchestration:
    1.  `db`: PostgreSQL container equipped with `pgvector/pgvector:pg16` image.
    2.  `redis`: Redis caching node.
    3.  `backend`: FastAPI service run via `uvicorn backend.app.main:app`.
    4.  `frontend`: Vite web application.
*   [`Dockerfile.backend`](file:///c:/Users/Asus/Desktop/DashNews/Dockerfile.backend): A multi-stage Docker configuration that builds the React application, builds the Python virtual environment, and packages them together. Uvicorn serves the static build files from `dist/` directly, minimizing container overhead.
*   [`render.yaml`](file:///c:/Users/Asus/Desktop/DashNews/render.yaml): Declares blueprint configurations for Render.com. It mounts the unified FastAPI/React bundle as a web service alongside managed PostgreSQL and Redis components.
*   [`nginx.conf`](file:///c:/Users/Asus/Desktop/DashNews/nginx.conf): Fallback reverse-proxy routing requests on `/api` to the backend, and rendering frontend static assets for independent web container runs.
