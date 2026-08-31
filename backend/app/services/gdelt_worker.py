"""
GDELT 2.0 Events Ingestion Worker (Production)
================================================
Polls the GDELT 2.0 live endpoint every 15 minutes, downloads the raw
compressed Events CSV into memory, parses the 61-column schema, filters
to Drishya's target countries, and inserts records into the Article table.

Runs as an async background task inside the FastAPI lifespan.

Production hardening:
  - Dialect-agnostic INSERT (SQLite OR IGNORE / Postgres ON CONFLICT)
  - Three-layer dedup: intra-batch URL, DB GLOBALEVENTID, DB URL
  - Exponential backoff on repeated failures
  - ZIP size guard (50 MB max) to prevent OOM
  - All heavy imports at module level
  - Safe numeric parsing (no uncaught ValueError)
"""

import asyncio
import io
import logging
import uuid as _uuid
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

import httpx
from sqlalchemy import text, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.app.config import settings
from backend.app.database import get_db, Article
from backend.app.observability import metrics

logger = logging.getLogger("drishya.gdelt_worker")

# ---------------------------------------------------------------------------
# Limits & constants
# ---------------------------------------------------------------------------
MAX_ZIP_BYTES = 50 * 1024 * 1024  # 50 MB — GDELT exports are typically 50-200 KB
MAX_BACKOFF_SECONDS = 300  # 5 min ceiling on failure backoff
INITIAL_BACKOFF_SECONDS = 30
GDELT_LAST_UPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"

# ---------------------------------------------------------------------------
# GDELT 2.0 Events CSV — 61 columns, tab-delimited, no header row.
# ---------------------------------------------------------------------------
GDELT_COLUMNS: List[str] = [
    "GLOBALEVENTID", "SQLDATE", "MonthYear", "Year", "FractionDate",
    "Actor1Code", "Actor1Name", "Actor1CountryCode", "Actor1KnownGroupCode",
    "Actor1EthnicCode", "Actor1Religion1Code", "Actor1Religion2Code",
    "Actor1Type1Code", "Actor1Type2Code", "Actor1Type3Code",
    "Actor2Code", "Actor2Name", "Actor2CountryCode", "Actor2KnownGroupCode",
    "Actor2EthnicCode", "Actor2Religion1Code", "Actor2Religion2Code",
    "Actor2Type1Code", "Actor2Type2Code", "Actor2Type3Code",
    "IsRootEvent", "EventCode", "EventBaseCode", "EventRootCode", "QuadClass",
    "GoldsteinScale", "NumMentions", "NumSources", "NumArticles", "AvgTone",
    "Actor1Geo_Type", "Actor1Geo_FullName", "Actor1Geo_CountryCode",
    "Actor1Geo_ADM1Code", "Actor1Geo_ADM2Code",
    "Actor1Geo_Lat", "Actor1Geo_Long", "Actor1Geo_FeatureID",
    "Actor2Geo_Type", "Actor2Geo_FullName", "Actor2Geo_CountryCode",
    "Actor2Geo_ADM1Code", "Actor2Geo_ADM2Code",
    "Actor2Geo_Lat", "Actor2Geo_Long", "Actor2Geo_FeatureID",
    "ActionGeo_Type", "ActionGeo_FullName", "ActionGeo_CountryCode",
    "ActionGeo_ADM1Code", "ActionGeo_ADM2Code",
    "ActionGeo_Lat", "ActionGeo_Long", "ActionGeo_FeatureID",
    "DATEADDED", "SOURCEURL",
]

# ---------------------------------------------------------------------------
# FIPS-10 → ISO 3166-1 alpha-2 mapping for countries Drishya monitors.
# GDELT uses FIPS codes; the rest of the codebase uses ISO.
# ---------------------------------------------------------------------------
FIPS_TO_ISO: Dict[str, str] = {
    # South Asia
    "IN": "IN",   # India
    "PK": "PK",   # Pakistan
    "AF": "AF",   # Afghanistan
    "BG": "BD",   # Bangladesh  (FIPS ≠ ISO here)
    "NP": "NP",   # Nepal
    "BT": "BT",   # Bhutan
    "CE": "LK",   # Sri Lanka   (FIPS ≠ ISO here)
    "MV": "MV",   # Maldives
    # East / Southeast Asia
    "CH": "CN",   # China       (FIPS ≠ ISO here)
    "BM": "MM",   # Myanmar     (FIPS ≠ ISO here)
    "JA": "JP",   # Japan       (FIPS ≠ ISO here)
    "KS": "KR",   # South Korea (FIPS ≠ ISO here)
    "TW": "TW",   # Taiwan
    # West / Central Asia
    "IR": "IR",   # Iran
    "IS": "IL",   # Israel      (FIPS ≠ ISO here)
    "IQ": "IQ",   # Iraq
    "SY": "SY",   # Syria
    "YE": "YE",   # Yemen
    "SA": "SA",   # Saudi Arabia
    # Europe / Americas
    "US": "US",   # United States
    "RS": "RU",   # Russia      (FIPS ≠ ISO here)
    "UP": "UA",   # Ukraine     (FIPS ≠ ISO here)
    "GM": "DE",   # Germany     (FIPS ≠ ISO here)
    "UK": "GB",   # United Kingdom (FIPS ≠ ISO here)
    "FR": "FR",   # France
    # Africa
    "SO": "SO",   # Somalia
    "SU": "SD",   # Sudan       (FIPS ≠ ISO here)
    "LY": "LY",   # Libya
    # Oceania
    "AS": "AU",   # Australia   (FIPS ≠ ISO here)
    # Additional countries used by settings
    "KP": "KP",   # North Korea
    "VE": "VE",   # Venezuela
}

# Build the reverse: set of ISO codes we care about (from all settings lists)
_TARGET_ISO_CODES: Set[str] = set(
    settings.critical_countries
    + settings.high_countries
    + settings.medium_countries
    + settings.low_countries
)

# FIPS codes that map to our target ISO codes (for fast filtering).
# Excludes empty strings to avoid false-positive set intersections.
_TARGET_FIPS_CODES: Set[str] = {
    fips for fips, iso in FIPS_TO_ISO.items()
    if fips and iso in _TARGET_ISO_CODES
}

if not _TARGET_FIPS_CODES:
    logger.warning("[GDELT] No target FIPS codes configured. Ingestion will filter out all events.")

# ---------------------------------------------------------------------------
# GDELT EventRootCode → Drishya department mapping
# Reference: https://www.gdeltproject.org/data.html#forums
# ---------------------------------------------------------------------------
EVENT_ROOT_TO_DEPARTMENT: Dict[str, str] = {
    "0": "Political & Diplomatic",
    "1": "Political & Diplomatic",   # Formal statements
    "2": "Political & Diplomatic",   # Consultations
    "3": "Political & Diplomatic",   # Diplomatic cooperation
    "4": "Economic & Financial",     # Material cooperation
    "5": "Political & Diplomatic",   # Economic cooperation → political
    "6": "Political & Diplomatic",   # Military cooperation
    "7": "Political & Diplomatic",   # Political relations
    "8": "Social Affairs & Welfare", # Military events
    "9": "Technology & Cyber",       # Use of unconventional force
}

QUAD_CLASS_TO_DEPARTMENT: Dict[int, str] = {
    1: "Political & Diplomatic",   # Verbal cooperation
    2: "Economic & Financial",     # Material cooperation
    3: "Political & Diplomatic",   # Verbal conflict
    4: "Military & Defense",       # Material conflict
}


# ---------------------------------------------------------------------------
# Pure helper functions — all safe against malformed GDELT data.
# ---------------------------------------------------------------------------
def _safe_int(value: str, default: int = 0) -> int:
    """Parse an int from a GDELT field without raising."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _safe_float(value: str, default: float = 0.0) -> float:
    """Parse a float from a GDELT field without raising."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _derive_department(row: Dict[str, str]) -> str:
    """Map a GDELT event to a Drishya department category."""
    quad_class = _safe_int(row.get("QuadClass"))
    dept = QUAD_CLASS_TO_DEPARTMENT.get(quad_class)
    if dept:
        return dept
    root_code = (row.get("EventRootCode") or "")[:1]
    return EVENT_ROOT_TO_DEPARTMENT.get(root_code, "Unclassified")


def _derive_impact_level(row: Dict[str, str]) -> str:
    """
    Derive impact level from GoldsteinScale, NumMentions, and AvgTone.

    High:    Goldstein <= -7  OR  NumMentions >= 20  OR  AvgTone <= -8
    Medium:  Goldstein <= -4  OR  NumMentions >= 8   OR  AvgTone <= -5
    Normal:  everything else
    """
    goldstein = _safe_float(row.get("GoldsteinScale"))
    num_mentions = _safe_int(row.get("NumMentions"))
    avg_tone = _safe_float(row.get("AvgTone"))

    if goldstein <= -7 or num_mentions >= 20 or avg_tone <= -8:
        return "High Impact"
    if goldstein <= -4 or num_mentions >= 8 or avg_tone <= -5:
        return "Medium Impact"
    return "Normal Impact"


def _parse_gdelt_date(date_str: str) -> datetime:
    """Parse GDELT SQLDATE (YYYYMMDD) to a timezone-aware datetime."""
    try:
        return datetime.strptime(date_str.strip(), "%Y%m%d").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _extract_source_domain(url: str) -> str:
    """Extract a clean domain from a URL for the source field."""
    if not url:
        return "GDELT"
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or ""
        if domain.startswith("www."):
            domain = domain[4:]
        return domain or "GDELT"
    except Exception:
        return "GDELT"


def _build_headline(row: Dict[str, str]) -> str:
    """Build a synthetic headline from GDELT event fields."""
    actor1 = row.get("Actor1Name") or ""
    actor2 = row.get("Actor2Name") or ""
    action_geo = row.get("ActionGeo_FullName") or ""
    event_code = row.get("EventCode") or ""

    parts: List[str] = []
    if actor1 and actor2:
        parts.append(f"{actor1} & {actor2}")
    elif actor1:
        parts.append(actor1)
    if action_geo:
        parts.append(action_geo)
    if event_code:
        parts.append(f"(Code {event_code})")

    return " ".join(parts) if parts else "GDELT Event"


def _build_content(row: Dict[str, str]) -> str:
    """Build a content string from the GDELT event record."""
    actor1 = row.get("Actor1Name") or ""
    actor2 = row.get("Actor2Name") or ""
    action_geo = row.get("ActionGeo_FullName") or ""
    source_url = row.get("SOURCEURL") or ""

    parts: List[str] = []
    if actor1 and actor2:
        parts.append(f"{actor1} and {actor2}")
    elif actor1:
        parts.append(actor1)
    if action_geo:
        parts.append(f"in {action_geo}")

    parts.append(
        f"Goldstein={row.get('GoldsteinScale') or '0'}, "
        f"Tone={row.get('AvgTone') or '0'}, "
        f"Mentions={row.get('NumMentions') or '0'}, "
        f"Sources={row.get('NumSources') or '0'}, "
        f"Articles={row.get('NumArticles') or '0'}"
    )

    if source_url:
        parts.append(f"Source: {source_url}")

    return ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# Main ingestion function — called by the background loop.
# ---------------------------------------------------------------------------
async def ingest_gdelt_events() -> Dict[str, int]:
    """
    Fetch the latest GDELT 2.0 Events update, filter to target countries,
    and insert matching records into the high_impact_articles table.

    Returns a summary dict with counts.
    """
    stats: Dict[str, int] = {
        "fetched": 0,
        "filtered": 0,
        "deduped": 0,
        "inserted": 0,
        "errors": 0,
    }

    logger.info("[GDELT] Polling for new GDELT 2.0 Events data...")

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=httpx.Timeout(30.0, connect=10.0)
        ) as client:
            # ── 1. Fetch lastupdate.txt to find the export ZIP URL ──
            resp = await client.get(GDELT_LAST_UPDATE_URL, timeout=15.0)
            resp.raise_for_status()

            lines = resp.text.strip().split("\n")
            export_url: Optional[str] = None
            for line in lines:
                if "export.CSV.zip" in line:
                    parts = line.strip().split()
                    if len(parts) >= 3:
                        export_url = parts[2]
                    break

            if not export_url:
                logger.error("[GDELT] Could not find export URL in lastupdate.txt")
                return stats

            logger.info("[GDELT] Downloading %s", export_url)

            # ── 2. Download ZIP into memory (with size guard) ──
            zip_resp = await client.get(export_url, timeout=60.0)
            zip_resp.raise_for_status()

            if len(zip_resp.content) > MAX_ZIP_BYTES:
                logger.error(
                    "[GDELT] ZIP exceeds size limit: %d bytes (max %d)",
                    len(zip_resp.content), MAX_ZIP_BYTES,
                )
                stats["errors"] += 1
                return stats

            # ── 3. Parse the CSV from the ZIP (tab-delimited, no header) ──
            with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
                if not zf.namelist():
                    logger.error("[GDELT] ZIP file is empty")
                    stats["errors"] += 1
                    return stats
                csv_name = zf.namelist()[0]
                with zf.open(csv_name) as csv_file:
                    raw = csv_file.read().decode("utf-8", errors="replace")
                    lines = raw.strip().split("\n")

            # Parse rows (skip header if GDELT mistakenly includes one)
            rows: List[Dict[str, str]] = []
            start_idx = 0
            if lines and lines[0].startswith("GLOBALEVENTID"):
                start_idx = 1

            for line in lines[start_idx:]:
                fields = line.split("\t")
                if len(fields) < len(GDELT_COLUMNS):
                    continue
                rows.append(dict(zip(GDELT_COLUMNS, fields)))

            stats["fetched"] = len(rows)
            logger.info("[GDELT] Parsed %d raw events from ZIP", len(rows))

            # ── 4. Filter to target countries ──
            filtered_rows: List[Dict[str, str]] = []
            for row in rows:
                action_fips = (row.get("ActionGeo_CountryCode") or "").strip()
                actor1_fips = (row.get("Actor1CountryCode") or "").strip()
                actor2_fips = (row.get("Actor2CountryCode") or "").strip()

                # Only check non-empty FIPS codes
                involved_fips = {f for f in (action_fips, actor1_fips, actor2_fips) if f}
                if involved_fips & _TARGET_FIPS_CODES:
                    filtered_rows.append(row)

            logger.info(
                "[GDELT] Filtered to %d events matching target countries (from %d total)",
                len(filtered_rows), len(rows),
            )

            if not filtered_rows:
                return stats

            # ── 5. Convert to Article-compatible dicts ──
            candidate_articles: List[Dict] = []
            gdelt_ids: List[str] = []
            for row in filtered_rows:
                fips_code = (row.get("ActionGeo_CountryCode") or "").strip()
                iso_code = FIPS_TO_ISO.get(fips_code) or fips_code

                source_url = (row.get("SOURCEURL") or "").strip()
                if not source_url or not source_url.startswith("http"):
                    continue

                event_id = (row.get("GLOBALEVENTID") or "").strip()

                candidate_articles.append({
                    "title": _build_headline(row),
                    "headline": _build_headline(row),
                    "summary": _build_content(row)[:500],
                    "content": _build_content(row),
                    "url": source_url,
                    "source": f"GDELT ({_extract_source_domain(source_url)})",
                    "country_code": iso_code,
                    "published_at": _parse_gdelt_date(row.get("SQLDATE") or ""),
                    "impact_level": _derive_impact_level(row),
                    "department": _derive_department(row),
                    "gdelt_event_id": event_id,
                })
                if event_id:
                    gdelt_ids.append(event_id)

            stats["filtered"] = len(candidate_articles)

            # ── 6a. Intra-batch URL dedup ──
            seen_urls: Set[str] = set()
            unique_articles: List[Dict] = []
            for a in candidate_articles:
                if a["url"] not in seen_urls:
                    seen_urls.add(a["url"])
                    unique_articles.append(a)
            deduped_batch = len(candidate_articles) - len(unique_articles)
            candidate_articles = unique_articles
            if deduped_batch:
                logger.info("[GDELT] Removed %d intra-batch URL duplicates", deduped_batch)

            # ── 6b. DB GLOBALEVENTID dedup ──
            async for db in get_db():
                if gdelt_ids:
                    existing_ids: Set[str] = set()
                    chunk_size = 500  # SQLite parameter limit safety
                    for i in range(0, len(gdelt_ids), chunk_size):
                        chunk = gdelt_ids[i : i + chunk_size]
                        result = await db.execute(
                            select(Article.gdelt_event_id).where(
                                Article.gdelt_event_id.in_(chunk)
                            )
                        )
                        existing_ids.update(
                            row[0] for row in result.all() if row[0]
                        )

                    before = len(candidate_articles)
                    candidate_articles = [
                        a for a in candidate_articles
                        if not a.get("gdelt_event_id")
                        or a["gdelt_event_id"] not in existing_ids
                    ]
                    stats["deduped"] += before - len(candidate_articles)
                break

            # ── 6c. DB URL dedup ──
            async for db in get_db():
                if candidate_articles:
                    all_urls = [a["url"] for a in candidate_articles]
                    existing_urls: Set[str] = set()
                    for i in range(0, len(all_urls), 500):
                        chunk = all_urls[i : i + 500]
                        result = await db.execute(
                            select(Article.url).where(Article.url.in_(chunk))
                        )
                        existing_urls.update(
                            row[0] for row in result.all() if row[0]
                        )
                    if existing_urls:
                        before = len(candidate_articles)
                        candidate_articles = [
                            a for a in candidate_articles
                            if a["url"] not in existing_urls
                        ]
                        stats["deduped"] += before - len(candidate_articles)
                break

            if not candidate_articles:
                logger.info("[GDELT] No new unique events to insert after dedup.")
                return stats

            # ── 7. Direct DB insert ──
            from backend.app.services.classifier import compute_source_reputation

            # Detect dialect once for the entire insert
            is_postgres = False
            async for db in get_db():
                is_postgres = db.bind.dialect.name == "postgresql"
                break

            inserted = 0
            async for db in get_db():
                for offset in range(0, len(candidate_articles), settings.ingestion_batch_size):
                    batch = candidate_articles[offset : offset + settings.ingestion_batch_size]
                    mappings = []
                    for art in batch:
                        source = art.get("source") or "GDELT"
                        mappings.append({
                            "id": str(_uuid.uuid4()),
                            "title": art["title"],
                            "headline": art.get("headline") or art["title"],
                            "summary": art.get("summary"),
                            "content": art["content"],
                            "url": art["url"],
                            "source": source,
                            "country_code": art["country_code"],
                            "published_at": art["published_at"],
                            "impact_level": art["impact_level"],
                            "department": art["department"],
                            "source_reputation": compute_source_reputation(source),
                            "confidence_score": 0.95,
                            "gdelt_event_id": art.get("gdelt_event_id"),
                        })

                    # Dialect-agnostic insert with conflict handling
                    if is_postgres:
                        # Postgres: ON CONFLICT (url) DO NOTHING
                        stmt = pg_insert(Article).values(mappings).on_conflict_do_nothing(
                            index_elements=["url"]
                        )
                    else:
                        # SQLite: INSERT OR IGNORE
                        stmt = sqlite_insert(Article).values(mappings).prefix_with("OR IGNORE")

                    await db.execute(stmt)
                    await db.commit()
                    inserted += len(mappings)

                break

            stats["inserted"] = inserted
            logger.info("[GDELT] Successfully inserted %d events into database.", inserted)

    except Exception as e:
        stats["errors"] += 1
        logger.error("[GDELT] Ingestion error: %s", e, exc_info=True)

    # ── Bump Prometheus counters ──
    metrics.state.gdelt_cycles_total += 1
    metrics.state.gdelt_events_fetched_total += stats["fetched"]
    metrics.state.gdelt_events_filtered_total += stats["filtered"]
    metrics.state.gdelt_events_deduped_total += stats["deduped"]
    metrics.state.gdelt_events_inserted_total += stats["inserted"]
    metrics.state.gdelt_ingestion_errors_total += stats["errors"]

    return stats


# ---------------------------------------------------------------------------
# Background loop — runs inside the FastAPI lifespan.
# ---------------------------------------------------------------------------
async def gdelt_ingestion_loop(interval_seconds: int = 900):
    """
    Background task that polls GDELT every `interval_seconds` (default 15 min).

    Implements exponential backoff on consecutive failures so a persistently
    broken GDELT endpoint does not hammer the network or logs.
    """
    logger.info("[GDELT] Background ingestion loop started (interval=%ds).", interval_seconds)

    backoff = INITIAL_BACKOFF_SECONDS

    # Run once immediately on startup
    try:
        stats = await ingest_gdelt_events()
        logger.info("[GDELT] Initial ingestion complete: %s", stats)
        if stats.get("errors", 0) == 0:
            backoff = INITIAL_BACKOFF_SECONDS  # reset on success
    except Exception as e:
        logger.error("[GDELT] Initial ingestion failed: %s", e)

    # Then run on the interval
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            stats = await ingest_gdelt_events()
            logger.info("[GDELT] Scheduled ingestion complete: %s", stats)
            if stats.get("errors", 0) == 0:
                backoff = INITIAL_BACKOFF_SECONDS  # reset on success
            else:
                # Increase backoff on failure, capped at MAX
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                logger.warning(
                    "[GDELT] Cycle had errors. Backing off %ds before next attempt.",
                    backoff,
                )
                await asyncio.sleep(backoff)
        except Exception as e:
            logger.error("[GDELT] Scheduled ingestion failed: %s", e)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            await asyncio.sleep(backoff)
