# Operational System Report: Drishya (OsINTDash)

This report details the system architecture, code organization, data ingestion pipeline, security model, and runtime capabilities of **Drishya (OsINTDash)**.

---

## 1. System Architecture

Drishya is architected as a decoupled, resilient tactical web application comprising:
1. **React 18 / TypeScript SPA Frontend**: Telemetry UI compiled via Vite, featuring interactive D3 geospatial map overlays, weather boundary HUDs, real-time alert ticker, and live WebSocket streaming.
2. **FastAPI Asynchronous Backend Ingestion Mesh**: Python asynchronous server running Uvicorn that aggregates, deduplicates, classifies, and broadcasts geopolitical signals across 30+ countries.
3. **Dual-Database Resilient Pipeline**: Production PostgreSQL database with `pgvector` embeddings, paired with automatic zero-configuration SQLite local database fallback (`articles_v2.db`).
4. **Redis Message Broker & Local In-Memory Fallback**: Redis Pub/Sub for distributed live alert broadcasting with automatic fallback to in-memory event queues when offline.

```mermaid
graph TD
  User[Browser Client / Analyst] -- 1. Authenticates via WebAuthn MFA --> App[React 18 / Vite SPA]
  App -- 2. REST Queries & SSE --> FastAPI[FastAPI Server: main.py]
  App -- 3. Live WebSocket Push --> WS[/ws WebSocket Stream]

  subgraph Ingestion & Processing Mesh
    FastAPI -- 4. Periodic Ingestion Loop (Every 60s) --> Ingestion[Ingestion Engine: ingestion.py]
    Ingestion -- 5. Parallel Query across 10+ Providers --> NewsAPIs[NewsAPI, GNews, Currents, Mediastack, etc.]
    Ingestion -- 6. Parallel Fallback Scraping --> GoogleRSS[Google News RSS / World Feeds]
    
    NewsAPIs --> Classifier[Classifier & NLP Engine: classifier.py]
    GoogleRSS --> Classifier
    
    Classifier -- 7. SHA-256 Deduplication & Regex Tagging --> DB[(SQLite / PostgreSQL DB)]
    Classifier -- 8. Publishes Signal Updates --> Redis[(Redis Pub/Sub Store)]
  end
  
  subgraph Geopolitical Intelligence Chatbot & RAG
    App -- 9. Prompts / Uploads Intelligence Files --> ChatRouter[Routes: chat.py / summarizer.py]
    ChatRouter -- 10. Document Text Extraction (PDF / DOCX / TXT) --> SimpleText[Extracted Content]
    SimpleText -- 11. Vector & TF-IDF Similarity Search --> DB
    SimpleText & DB -- 12. Context Augmentation --> LLM{Ollama: Llama 3.1 / Local Fallback}
    LLM -- 13. Formatted Intelligence Briefing --> App
  end

  Redis --> WS
  WS --> App
```

---

## 2. Ingestion, Classification & Noise Filtering Engine

To ensure raw OSINT feeds meet strict intelligence standards, the backend executes a multi-stage filtering and enrichment pipeline:

1. **Noise & False-Positive Elimination**: Uses strict word-boundary regular expressions (`\b...s?\b`) to prevent false-positive keyword collisions on common English terms.
2. **Normalized Deduplication**: Generates deterministic SHA-256 hashes from normalized title strings and sources to reject duplicate wire releases.
3. **Multi-Source Fallback Chain with Circuit Breakers**:
   - Primary: NewsAPI.org, World News API, NewsData.io
   - Secondary: GNews.io, Currents API, TheNewsAPI, Mediastack, Newscatcher, Bing News
   - Universal Fallback: Google News RSS feeds parsed via `googlenewsdecoder` and `selectolax`
4. **Adaptive Rate-Limiting & Exponential Backoff**: Automatically detects HTTP 429/401 errors, applies jittered backoff, and trips circuit breakers for failing providers without interrupting feed delivery.

---

## 3. Directory Structure

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
│   ├── requirements.txt      # Python dependencies
│   └── run.py                # Server launcher script
├── src/                      # React 18 + TypeScript Frontend
│   ├── components/           # UI components: Map, Hud, Dossier, Chat, Notes, Alerts
│   ├── services/             # API clients & WebSocket connection handlers
│   ├── App.tsx               # Main application layout and view router
│   ├── main.tsx              # React mounting root
│   └── styles.css            # Tactical HUD styling, animations, and scanline shaders
├── data/                     # Local SQLite database fallback storage
├── Dockerfile.backend        # Unified container definition
├── Dockerfile.frontend       # Nginx container definition
├── docker-compose.yml        # Docker compose stack
├── package.json              # Project scripts and dependencies
├── render.yaml               # Cloud blueprint
└── vite.config.ts            # Vite proxy & build config
```

---

## 4. Key UI Widgets & User Workflows

* **Security Access Gate (STRATCOM WebAuthn MFA)**: Authenticates user credentials (`analyst@intel.local`, `operator@intel.local`, `admin@intel.local`) with simulated cryptographic biometric handshake.
* **D3 Live Map & Meteorological HUD**: Visualizes active threat sectors and 15 border meteorological weather stations with live temperatures, wind metrics, and barometric alerts.
* **RAG Intelligence Synthesis (AI Summarizer)**: Ingests uploaded PDF, DOCX, TXT documents or URL links, fuses them with database news wires, and synthesizes strategic intelligence briefings.
* **Ephemeral Shared Notes**: Operator whiteboard with 24-hour self-destruct countdown timers and 1-click "Pin to Notes" from any telemetry card.
