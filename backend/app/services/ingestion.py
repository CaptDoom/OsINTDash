import json
from pathlib import Path
import asyncio
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote_plus

import httpx

from backend.app.config import settings

logger = logging.getLogger("drishya.ingestion")


ISO_COUNTRIES: Dict[str, str] = {
    "CN": "China",
    "PK": "Pakistan",
    "AF": "Afghanistan",
    "BD": "Bangladesh",
    "MM": "Myanmar",
    "NP": "Nepal",
    "BT": "Bhutan",
    "LK": "Sri Lanka",
    "MV": "Maldives",
    "IN": "India",
    "US": "United States",
    "RU": "Russia",
    "UA": "Ukraine",
    "IR": "Iran",
    "IL": "Israel",
    "TW": "Taiwan",
    "JP": "Japan",
    "GB": "United Kingdom",
    "FR": "France",
    "DE": "Germany",
    "KR": "South Korea",
}

# Dynamically load coordinates and names from world-countries npm package
PROJECT_ROOT = Path(__file__).resolve().parents[3]
COUNTRIES_JSON_PATH = PROJECT_ROOT / "node_modules" / "world-countries" / "countries.json"
if COUNTRIES_JSON_PATH.exists():
    try:
        with open(COUNTRIES_JSON_PATH, "r", encoding="utf-8") as f:
            countries_data = json.load(f)
            for c in countries_data:
                cca2 = c.get("cca2", "").upper()
                name = c.get("name", {}).get("common", "")
                if cca2 and name:
                    ISO_COUNTRIES[cca2] = name
    except Exception as e:
        logger.warning("[Ingestion] Failed to populate ISO_COUNTRIES from countries.json: %s", e)

for char1 in range(65, 91):
    for char2 in range(65, 91):
        code = chr(char1) + chr(char2)
        ISO_COUNTRIES.setdefault(code, f"Country_{code}")


class CircuitState:
    def __init__(self, source_name: str, redis_url: Optional[str] = None):
        self.source_name = source_name
        self.redis_url = redis_url
        self._failures = 0
        self._open_until = 0.0
        self._rate_limit_failures = 0

    async def _get_pool(self):
        """Get shared Redis pool, or None if unavailable."""
        if not self.redis_url or not settings.enable_redis_breaker_persistence:
            return None
        try:
            from backend.app.redis_pool import get_redis_pool
            return await get_redis_pool()
        except Exception:
            return None

    async def get_failures(self) -> int:
        pool = await self._get_pool()
        if pool:
            try:
                val = await pool.get(f"drishya:breaker:{self.source_name}:failures")
                return int(val) if val else 0
            except Exception:
                pass
        return self._failures

    async def get_open_until(self) -> float:
        pool = await self._get_pool()
        if pool:
            try:
                val = await pool.get(f"drishya:breaker:{self.source_name}:open_until")
                return float(val) if val else 0.0
            except Exception:
                pass
        return self._open_until

    async def allow(self) -> bool:
        open_until = await self.get_open_until()
        return asyncio.get_event_loop().time() >= open_until

    async def record_failure(self, cooldown_seconds: float) -> None:
        failures = await self.get_failures() + 1
        open_until = 0.0
        if failures >= settings.circuit_breaker_failure_threshold:
            open_until = asyncio.get_event_loop().time() + cooldown_seconds

        pool = await self._get_pool()
        if pool:
            try:
                ttl = int(cooldown_seconds) if open_until > 0 else 86400
                await pool.set(f"drishya:breaker:{self.source_name}:failures", str(failures), ex=ttl)
                if open_until > 0:
                    await pool.set(f"drishya:breaker:{self.source_name}:open_until", str(open_until), ex=ttl)
                return
            except Exception:
                pass
        self._failures = failures
        if open_until > 0:
            self._open_until = open_until

    async def set_tripped(self, cooldown_seconds: float) -> None:
        open_until = asyncio.get_event_loop().time() + cooldown_seconds
        failures = settings.circuit_breaker_failure_threshold
        pool = await self._get_pool()
        if pool:
            try:
                ttl = int(cooldown_seconds)
                await pool.set(f"drishya:breaker:{self.source_name}:failures", str(failures), ex=ttl)
                await pool.set(f"drishya:breaker:{self.source_name}:open_until", str(open_until), ex=ttl)
                return
            except Exception:
                pass
        self._failures = failures
        self._open_until = open_until

    async def record_rate_limit(self) -> bool:
        """Track throttling separately from hard provider failures."""
        self._rate_limit_failures += 1
        if self._rate_limit_failures >= settings.circuit_breaker_rate_limit_threshold:
            await self.set_tripped(settings.circuit_breaker_rate_limit_cooldown_seconds)
            return True
        return False

    async def reset(self) -> None:
        pool = await self._get_pool()
        if pool:
            try:
                await pool.delete(f"drishya:breaker:{self.source_name}:failures")
                await pool.delete(f"drishya:breaker:{self.source_name}:open_until")
                return
            except Exception:
                pass
        self._failures = 0
        self._open_until = 0.0
        self._rate_limit_failures = 0


SOURCE_BREAKERS: Dict[str, CircuitState] = {}


def sanitize_article_text(text: str) -> str:
    if not text:
        return ""
    # Remove lines matching common boilerplate patterns (case-insensitive)
    lines = text.split("\n")
    cleaned_lines = []
    boilerplate_patterns = [
        r"sign up for (our )?newsletter",
        r"subscribe to (our )?newsletter",
        r"read more:",
        r"follow us on",
        r"copyright \u00a9",
        r"all rights reserved",
        r"contributed to this report",
        r"reporting by",
        r"editing by",
        r"click here to",
        r"cookie(s)? (policy|notice|settings)",
        r"privacy (policy|notice)",
        r"terms (of|&) (service|use|conditions)",
        r"advertisement",
        r"loading\.\.\.",
        r"please enable javascript",
    ]
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        line_lower = line_stripped.lower()
        if any(re.search(pat, line_lower) for pat in boilerplate_patterns):
            continue
        cleaned_lines.append(line_stripped)
        
    text = "\n".join(cleaned_lines)
    
    # Strip wire service prefixes at the beginning of the text, e.g. "REUTERS -", "AP -", "NEW DELHI (Reuters) -"
    wire_pattern = r"^\s*(?:[A-Z\s]+(?:\([A-Z\s,]+\))?\s*[-—]\s*|[A-Z\s]+(?:\([A-Z\s,]+\))?\s*-\s*|[A-Z\s]+\s*:\s*)"
    text = re.sub(wire_pattern, "", text, count=1)
    
    return text.strip()


def is_article_fresh(published_at: datetime, max_age_days: int = 7) -> bool:
    """Check if an article is within the freshness window."""
    if not published_at:
        return False
    age = datetime.now(timezone.utc) - published_at
    return age.days <= max_age_days


def has_minimum_content_quality(title: str, content: str, summary: str = "") -> bool:
    """Validate that an article has minimum content quality."""
    # Title must exist and be at least 10 characters
    if not title or len(title.strip()) < 10:
        return False
    # Content or summary must have at least 50 characters of real text
    text = (content or summary or "").strip()
    if len(text) < 50:
        return False
    # Reject articles that are mostly boilerplate/placeholder
    words = text.split()
    if len(words) < 8:
        return False
    # Reject articles with too many repeated words (spam/placeholder)
    word_set = set(w.lower() for w in words)
    if len(word_set) < len(words) * 0.3 and len(words) > 15:
        return False
    return True


async def scrape_full_text(client: httpx.AsyncClient, url: str) -> str:
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return ""
    import trafilatura
    try:
        # Fetch HTML asynchronously with realistic User-Agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = await client.get(url, timeout=10.0, headers=headers, follow_redirects=True)
        if response.status_code == 200:
            # Extract main text using trafilatura
            text = trafilatura.extract(response.text)
            if text:
                cleaned = sanitize_article_text(text)
                # Keep first 1,500 words
                words = cleaned.split()
                if len(words) > 1500:
                    cleaned = " ".join(words[:1500])
                return cleaned
    except Exception as e:
        logger.debug(f"[Ingestion] Failed to scrape full text from {url}: {e}")
    return ""



def encode_query(query: str) -> str:
    return quote_plus(query)


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(value[:25].strip(), "%a, %d %b %Y %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)


def _priority_codes() -> List[str]:
    # Only scan watchlisted countries to prevent rate limits and network bloat
    config_codes = list(dict.fromkeys(settings.critical_countries + settings.high_countries + settings.medium_countries + settings.low_countries))
    return config_codes


def _country_priority(code: str) -> str:
    if code in settings.critical_countries:
        return "critical"
    if code in settings.high_countries:
        return "high"
    if code in settings.medium_countries:
        return "medium"
    return "low"


def _should_refresh(priority: str) -> int:
    if priority == "critical":
        return settings.country_refresh_minutes_critical
    if priority == "high":
        return settings.country_refresh_minutes_high
    if priority == "medium":
        return settings.country_refresh_minutes_medium
    return settings.country_refresh_minutes_low


def get_clear_source(url: str | None, current_source: str | None) -> str:
    from urllib.parse import urlparse
    src = (current_source or "").strip()
    generic_sources = {"rss", "newsapi", "gdelt", "unknown", "none", "osint", "osint-mesh", "feed", "api", "web", "internet"}
    if src and src.lower() not in generic_sources:
        return src
    if url:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc
            if domain:
                if domain.startswith("www."):
                    domain = domain[4:]
                return domain
        except Exception:
            pass
    return src or "OSINT Mesh"


def _parse_rss_items(xml_text: str, country_code: str, country_name: str, limit: int) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            return []
        items = []
        for item in channel.findall("item")[:limit]:
            title = item.findtext("title") or "Untitled"
            link = item.findtext("link") or ""
            desc = item.findtext("description") or ""
            source = get_clear_source(link, item.findtext("source") or "RSS")
            if "<" in desc:
                desc = re.sub("<[^<]+?>", "", desc)
            items.append(
                {
                    "title": title,
                    "headline": title,
                    "content": desc or title,
                    "url": link,
                    "source": source,
                    "country_code": country_code,
                    "country_name": country_name,
                    "published_at": _parse_datetime(item.findtext("pubDate")),
                }
            )
        return items
    except Exception as exc:
        logger.debug("[Ingestion] RSS parse failed for %s: %s", country_name, exc)
        return []


def _normalize_gdelt_record(article: Dict[str, Any], country_code: str, country_name: str) -> Dict[str, Any]:
    title = article.get("title") or article.get("seendate") or "Untitled"
    url = article.get("url") or ""
    source = get_clear_source(url, article.get("sourceCountry") or article.get("domain") or "GDELT")
    return {
        "title": title,
        "headline": title,
        "content": article.get("excerpt") or article.get("content") or article.get("title") or "",
        "summary": article.get("excerpt") or article.get("title") or "",
        "url": url,
        "source": source,
        "country_code": country_code,
        "country_name": country_name,
        "published_at": _parse_datetime(article.get("seendate") or article.get("datetime")),
    }


async def notify_circuit_breaker(source_name: str, action: str, reason: str):
    """
    Publishes a circuit breaker state change to Redis 'live_stream' channel
    so that WebSocket clients are instantly notified.
    """
    payload = {
        "type": "mesh_status",
        "status": "degraded_mesh" if action == "tripped" else "normal",
        "source": source_name,
        "action": action,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    try:
        from backend.app.services.classifier import memory_stream
        memory_stream.publish(payload)
    except Exception:
        pass
    
    try:
        from backend.app.redis_pool import pubsub_publish
        await pubsub_publish("live_stream", payload)
    except Exception as e:
        logger.debug("[Ingestion] Redis breaker notification failed: %s", e)


async def _get_with_retry(client: httpx.AsyncClient, url: str, *, source_name: str, breaker: CircuitState, params: Optional[dict] = None, headers: Optional[dict] = None) -> Optional[httpx.Response]:
    if not await breaker.allow():
        return None

    import random
    delay = settings.request_backoff_base_seconds
    max_attempts = max(
        settings.request_retry_count,
        settings.rate_limit_retry_count,
        settings.server_error_retry_count,
    )
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.get(url, params=params, headers=headers, timeout=settings.request_timeout_seconds)
            logger.info("[Ingestion] provider=%s status=%s attempt=%s", source_name, response.status_code, attempt)
            if response.status_code in (401, 403):
                logger.warning("[Ingestion] provider=%s authentication/permission failure HTTP %d; disabling for %ss", source_name, response.status_code, settings.circuit_breaker_auth_cooldown_seconds)
                await breaker.set_tripped(settings.circuit_breaker_auth_cooldown_seconds)
                await notify_circuit_breaker(source_name, "tripped", f"HTTP {response.status_code} Unauthorized")
                return response
            
            if response.status_code == 429:
                logger.warning("[Ingestion] %s rate limited (HTTP 429). Applying backoff with jitter.", source_name)
                jitter = random.uniform(0.5, 1.5)
                sleep_time = min(delay * jitter, settings.request_backoff_max_seconds)
                if attempt >= settings.rate_limit_retry_count:
                    tripped = await breaker.record_rate_limit()
                    logger.warning("[Ingestion] provider=%s rate limited after %s attempts; breaker_tripped=%s", source_name, attempt, tripped)
                    if tripped:
                        await notify_circuit_breaker(source_name, "tripped", "Rate Limit Exceeded (HTTP 429)")
                    return None
                await asyncio.sleep(sleep_time)
                delay *= 2
                continue
                
            if 500 <= response.status_code < 600:
                if attempt >= settings.server_error_retry_count:
                    await breaker.record_failure(settings.circuit_breaker_server_error_cooldown_seconds)
                    logger.warning("[Ingestion] provider=%s server error persisted after %s attempts; skipping", source_name, attempt)
                    return None
                request = getattr(response, "request", None) or httpx.Request("GET", url)
                raise httpx.HTTPStatusError("server error", request=request, response=response)
            
            # Reset and notify recovery if previously tripped
            was_tripped = await breaker.get_failures() >= settings.circuit_breaker_failure_threshold
            await breaker.reset()
            if was_tripped:
                await notify_circuit_breaker(source_name, "recovered", "Requests succeeded")
                
            return response
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            logger.info("[Ingestion] %s connection/DNS failure (attempt %s/%s): %s", source_name, attempt, settings.request_retry_count, exc)
            if attempt == settings.request_retry_count:
                logger.warning("[Ingestion] %s connection/DNS failure after max retries. Tripping circuit breaker for 5m.", source_name)
                await breaker.set_tripped(300)
                await notify_circuit_breaker(source_name, "tripped", "Connection/DNS Failure")
                return None
            await asyncio.sleep(min(delay * random.uniform(0.5, 1.5), settings.request_backoff_max_seconds))
            delay *= 2
        except Exception as exc:
            logger.info("[Ingestion] %s request failed (attempt %s/%s): %s", source_name, attempt, settings.request_retry_count, exc)
            was_tripped_before = await breaker.get_failures() >= 3
            await breaker.record_failure(settings.request_backoff_max_seconds)
            if (await breaker.get_failures()) >= settings.circuit_breaker_failure_threshold and not was_tripped_before:
                await notify_circuit_breaker(source_name, "tripped", "Max Retries Exceeded")
                
            if attempt == settings.request_retry_count:
                return None
            await asyncio.sleep(min(delay * random.uniform(0.5, 1.5), settings.request_backoff_max_seconds))
            delay *= 2
    return None



def _normalize_api_article(article: Dict[str, Any], country_code: str, country_name: str, source_label: str) -> Dict[str, Any]:
    url = article.get("url") or article.get("link") or article.get("canonical_url") or article.get("feed_url") or ""
    published_at = _parse_datetime(
        article.get("publishedAt")
        or article.get("pubDate")
        or article.get("published_at")
        or article.get("published_at_utc")
        or article.get("published")
    )
    summary = article.get("description") or article.get("summary") or article.get("content") or article.get("excerpt") or article.get("title") or ""
    content = article.get("content") or article.get("description") or article.get("summary") or article.get("excerpt") or article.get("title") or ""
    source = source_label
    if isinstance(article.get("source"), dict):
        source = article.get("source", {}).get("name") or source_label
    elif article.get("source"):
        source = article.get("source")

    source = get_clear_source(url, source)

    return {
        "title": article.get("title") or article.get("headline") or article.get("name") or "Untitled",
        "headline": article.get("title") or article.get("headline") or article.get("name") or "Untitled",
        "content": content,
        "summary": summary,
        "url": url,
        "source": source,
        "country_code": country_code,
        "country_name": country_name,
        "published_at": published_at,
    }


def _search_query(country_name: str) -> str:
    """Build a high-relevance search query per country."""
    # Country-specific high-value topics
    country_topics = {
        "China": 'China AND (Taiwan OR \"South China Sea\" OR LAC OR border OR military OR trade OR sanctions OR tariffs OR technology OR semiconductor OR Xinjiang OR Hong Kong)',
        "Pakistan": 'Pakistan AND (military OR border OR terrorism OR ceasefire OR Kashmir OR NATO OR Afghanistan OR Balochistan OR nuclear OR India)',
        "India": 'India AND (military OR border OR LAC OR Kashmir OR defense OR nuclear OR election OR economy OR technology OR space)',
        "Russia": 'Russia AND (Ukraine OR war OR sanctions OR military OR NATO OR oil OR gas OR nuclear OR ceasefire OR frontline)',
        "Ukraine": 'Ukraine AND (war OR military OR Russia OR frontline OR NATO OR aid OR ceasefire OR missile OR drone OR offensive)',
        "Iran": 'Iran AND (nuclear OR sanctions OR military OR proxy OR drone OR Middle East OR Israel OR missile OR IRGC)',
        "Israel": 'Israel AND (Gaza OR military OR Hamas OR Hezbollah OR defense OR missile OR ceasefire OR Iron Dome OR drone)',
        "Taiwan": 'Taiwan AND (China OR military OR defense OR strait OR semiconductor OR TSMC OR invasion OR blockade)',
        "Japan": 'Japan AND (military OR defense OR China OR North Korea OR missile OR alliance OR AUKUS OR economy)',
        "South Korea": '\"South Korea\" AND (North Korea OR military OR missile OR defense OR denuclearization OR economy)',
        "Myanmar": 'Myanmar AND (conflict OR military OR coup OR resistance OR ethnic OR border OR humanitarian OR sanctions)',
        "Afghanistan": 'Afghanistan AND (Taliban OR security OR border OR humanitarian OR Pakistan OR ISIS OR women OR refugees)',
        "Bangladesh": 'Bangladesh AND (politics OR election OR economy OR border OR security OR Rohingya OR migration)',
        "Nepal": 'Nepal AND (politics OR economy OR border OR India OR China OR infrastructure OR earthquake)',
        "Sri Lanka": '\"Sri Lanka\" AND (economy OR debt OR port OR China OR India OR politics OR fisheries)',
        "United States": 'United States AND (military OR NATO OR China OR sanctions OR economy OR election OR technology OR diplomacy)',
    }
    if country_name in country_topics:
        return country_topics[country_name]
    # Fallback: broad news query
    return f'{country_name} AND (news OR politics OR economy OR security OR military OR conflict OR government)'


def _search_query_broad(country_name: str, level: int = 0) -> str:
    if level == 0:
        return _search_query(country_name)
    elif level == 1:
        return f'{country_name} AND (news OR politics OR economy OR military)'
    else:
        return f'{country_name}'



async def fetch_newsapi_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.newsapi_key:
        return []

    for level in range(3):
        params = {
            "q": _search_query_broad(country_name, level),
            "language": "en",
            "pageSize": limit,
            "sortBy": "publishedAt",
            "apiKey": settings.newsapi_key,
        }
        response = await _get_with_retry(client, "https://newsapi.org/v2/everything", source_name="NewsAPI", breaker=breaker, params=params)
        if not response or response.status_code != 200:
            continue

        data = response.json()
        articles = data.get("articles") or []
        if articles:
            return [
                _normalize_api_article(article, country_code, country_name, article.get("source", {}).get("name", "NewsAPI"))
                for article in articles[:limit]
            ]
    return []


async def fetch_gnews_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.gnews_api_key:
        return []

    for level in range(3):
        params = {
            "q": _search_query_broad(country_name, level),
            "lang": "en",
            "max": limit,
            "token": settings.gnews_api_key,
        }
        response = await _get_with_retry(client, "https://gnews.io/api/v4/search", source_name="GNews", breaker=breaker, params=params)
        if not response or response.status_code != 200:
            continue

        data = response.json()
        articles = data.get("articles") or []
        if articles:
            return [
                _normalize_api_article(article, country_code, country_name, article.get("source", "GNews"))
                for article in articles[:limit]
            ]
    return []


async def fetch_newsdata_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.newsdata_api_key:
        return []

    # NewsData.io free tier keys reject complex boolean queries with a 422, so we use the country name directly
    params = {
        "apikey": settings.newsdata_api_key,
        "q": country_name,
        "language": "en",
        "size": limit,
    }
    response = await _get_with_retry(client, "https://newsdata.io/api/1/news", source_name="NewsData", breaker=breaker, params=params)
    if not response or response.status_code != 200:
        return []

    data = response.json()
    return [
        _normalize_api_article(article, country_code, country_name, article.get("source_id", "NewsData"))
        for article in data.get("results", [])[:limit]
    ]


async def fetch_worldnews_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.world_news_api_key:
        return []

    urls = [
        "https://api.worldnewsapi.com/search-news",
        "https://www.worldnewsapi.com/search-news",
    ]

    for level in range(3):
        params = {
            "key": settings.world_news_api_key,
            "language": "en",
            "q": _search_query_broad(country_name, level),
            "sort": "publish-time",
            "sort-direction": "DESC",
            "page": 1,
            "pageSize": limit,
        }
        for url in urls:
            response = await _get_with_retry(client, url, source_name="WorldNewsAPI", breaker=breaker, params=params)
            if not response or response.status_code != 200:
                continue

            try:
                data = response.json()
            except Exception as e:
                logger.error("[Ingestion] WorldNewsAPI returned non-JSON response: status %d. Error: %s. Response body: %s", response.status_code, e, response.text[:200])
                continue

            articles = data.get("articles") or data.get("data") or data.get("results") or []
            if not isinstance(articles, list) or not articles:
                continue

            normalized = [
                _normalize_api_article(article, country_code, country_name, article.get("source", "WorldNewsAPI"))
                for article in articles[:limit]
            ]
            if normalized:
                return normalized

    return []


async def fetch_finnhub_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.finnhub_api_key:
        return []

    params = {
        "category": "general",
        "token": settings.finnhub_api_key,
    }
    response = await _get_with_retry(client, "https://finnhub.io/api/v1/news", source_name="Finnhub", breaker=breaker, params=params)
    if not response or response.status_code != 200:
        return []

    data = response.json()
    return [
        _normalize_api_article(article, country_code, country_name, "Finnhub")
        for article in data[:limit]
    ]


async def fetch_currents_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.currents_api_key:
        return []

    for level in range(3):
        params = {
            "apiKey": settings.currents_api_key,
            "keywords": _search_query_broad(country_name, level),
            "language": "en",
            "limit": limit,
        }
        response = await _get_with_retry(client, "https://api.currentsapi.services/v1/search", source_name="Currents", breaker=breaker, params=params)
        if not response or response.status_code != 200:
            continue

        data = response.json()
        news = data.get("news") or []
        if news:
            return [
                _normalize_api_article(article, country_code, country_name, article.get("source", "Currents"))
                for article in news[:limit]
            ]
    return []


async def fetch_thenews_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.thenews_api_key:
        return []

    for level in range(3):
        params = {
            "api_token": settings.thenews_api_key,
            "language": "en",
            "search": _search_query_broad(country_name, level),
            "limit": limit,
        }
        response = await _get_with_retry(client, "https://api.thenewsapi.com/v1/news/all", source_name="TheNews", breaker=breaker, params=params)
        if not response or response.status_code != 200:
            continue

        data = response.json()
        data_list = data.get("data") or []
        if data_list:
            return [
                _normalize_api_article(article, country_code, country_name, article.get("source", "TheNews"))
                for article in data_list[:limit]
            ]
    return []


async def fetch_mediastack_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.mediastack_api_key:
        return []

    for level in range(3):
        params = {
            "access_key": settings.mediastack_api_key,
            "keywords": _search_query_broad(country_name, level),
            "languages": "en",
            "limit": limit,
        }
        response = await _get_with_retry(client, "http://api.mediastack.com/v1/news", source_name="Mediastack", breaker=breaker, params=params)
        if not response or response.status_code != 200:
            continue

        data = response.json()
        data_list = data.get("data") or []
        if data_list:
            return [
                _normalize_api_article(article, country_code, country_name, article.get("source", "Mediastack"))
                for article in data_list[:limit]
            ]
    return []


async def fetch_newscatcher_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.newscatcher_api_key:
        return []

    headers = {"x-api-token": settings.newscatcher_api_key}
    urls = [
        "https://v3-api.newscatcherapi.com/api/search",
        "https://api.newscatcherapi.com/v2/search"
    ]

    for level in range(3):
        params = {
            "q": _search_query_broad(country_name, level),
            "lang": "en",
            "page_size": limit,
        }
        for url in urls:
            response = await _get_with_retry(client, url, source_name="Newscatcher", breaker=breaker, params=params, headers=headers)
            if not response or response.status_code != 200:
                continue

            try:
                data = response.json()
            except Exception:
                continue

            articles_list = data.get("articles") or []
            if not isinstance(articles_list, list) or not articles_list:
                continue

            articles = []
            for article in articles_list[:limit]:
                article["published_at"] = article.get("published_date") or article.get("published_at")
                article["source"] = article.get("name_source") or article.get("source")
                articles.append(_normalize_api_article(article, country_code, country_name, "Newscatcher"))
            return articles

    return []


async def fetch_bing_news_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.bing_news_api_key:
        return []

    headers = {"Ocp-Apim-Subscription-Key": settings.bing_news_api_key}
    
    for level in range(3):
        params = {
            "q": _search_query_broad(country_name, level),
            "count": limit,
            "freshness": "Day",
            "textFormat": "Raw",
            "mkt": "en-US",
        }
        response = await _get_with_retry(client, "https://api.bing.microsoft.com/v7.0/news/search", source_name="BingNews", breaker=breaker, params=params, headers=headers)
        if not response or response.status_code != 200:
            continue

        data = response.json()
        value = data.get("value") or []
        if value:
            return [
                _normalize_api_article(article, country_code, country_name, article.get("provider", [{}])[0].get("name", "BingNews"))
                for article in value[:limit]
            ]
    return []


async def fetch_freenewsapi_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.freenewsapi_key:
        return []

    headers = {"x-api-key": settings.freenewsapi_key}
    params = {
        "in_title": country_name,
        "language": "en",
    }

    response = await _get_with_retry(client, "https://api.freenewsapi.io/v1/news", source_name="FreeNewsApi", breaker=breaker, params=params, headers=headers)
    if not response or response.status_code != 200:
        return []

    data = response.json()
    articles_list = data.get("data", [])[:limit]
    
    detailed_articles = []
    
    async def fetch_detail(article_summary: Dict[str, Any]):
        uuid = article_summary.get("uuid")
        if not uuid:
            return
        
        detail_params = {"uuid": uuid}
        detail_resp = await _get_with_retry(client, "https://api.freenewsapi.io/v1/details", source_name="FreeNewsApiDetails", breaker=breaker, params=detail_params, headers=headers)
        if detail_resp and detail_resp.status_code == 200:
            detail_data = detail_resp.json().get("data", {})
            if detail_data:
                detailed_articles.append(
                    _normalize_api_article(
                        {
                            "title": detail_data.get("title"),
                            "url": detail_data.get("original_url"),
                            "publishedAt": detail_data.get("published_at"),
                            "description": detail_data.get("incipit") or detail_data.get("title"),
                            "content": detail_data.get("body") or detail_data.get("incipit") or detail_data.get("title"),
                            "source": detail_data.get("publisher") or "FreeNewsApi"
                        },
                        country_code,
                        country_name,
                        detail_data.get("publisher") or "FreeNewsApi"
                    )
                )

    await asyncio.gather(*(fetch_detail(art) for art in articles_list), return_exceptions=True)
    return detailed_articles


async def fetch_gdelt_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    """Fetch from GDELT Project — free, no API key, excellent global coverage."""
    query = _search_query(country_name)
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": min(limit, 50),
        "sort": "DateDesc",
        "timespan": "1d",
    }
    response = await _get_with_retry(
        client, "https://api.gdeltproject.org/api/v2/doc/doc",
        source_name="GDELT", breaker=breaker, params=params,
    )
    if not response or response.status_code != 200:
        return []
    try:
        data = response.json()
    except Exception:
        return []
    articles_raw = data.get("articles") or []
    return [_normalize_gdelt_record(a, country_code, country_name) for a in articles_raw[:limit]]


async def _fetch_all_news_sources(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int) -> List[Dict[str, Any]]:
    source_limit = max(limit, 10)
    import difflib
    
    all_articles: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()

    def add_unique_articles(articles_list):
        for article in articles_list:
            if not article.get("title") or not article.get("url"):
                continue
            
            title = article["title"]
            content = article.get("content") or article.get("summary") or ""
            summary = article.get("summary") or ""
            
            # Reject articles that fail content quality check
            if not has_minimum_content_quality(title, content, summary):
                continue
            
            # Reject stale articles (older than 7 days)
            pub_date = article.get("published_at")
            if pub_date and not is_article_fresh(pub_date):
                continue
            
            # Country relevance check: article must mention the target country
            combined_text = f"{title} {content} {summary}".lower()
            country_lower = country_name.lower()
            if country_lower not in combined_text:
                # Also check country code
                if country_code.lower() not in combined_text:
                    continue
            
            url = article["url"]
            if url in seen_urls:
                continue
                
            # Fuzzy title match (threshold 0.85 to reduce false positives)
            is_dup = False
            norm_title = "".join(c for c in title.lower() if c.isalnum())
            for accepted in all_articles:
                acc_title = accepted.get("title", "")
                acc_norm = "".join(c for c in acc_title.lower() if c.isalnum())
                if norm_title == acc_norm or difflib.SequenceMatcher(None, title.lower(), acc_title.lower()).ratio() > 0.85:
                    is_dup = True
                    break
            
            # Content-based dedup: catch same content with different titles
            if not is_dup and len(content) > 100:
                content_norm = "".join(c for c in content.lower()[:500] if c.isalnum())
                for accepted in all_articles:
                    acc_content = accepted.get("content") or accepted.get("summary") or ""
                    acc_content_norm = "".join(c for c in acc_content.lower()[:500] if c.isalnum())
                    if content_norm and acc_content_norm and len(content_norm) > 50:
                        if difflib.SequenceMatcher(None, content_norm, acc_content_norm).ratio() > 0.90:
                            is_dup = True
                            break
            
            if not is_dup:
                seen_urls.add(url)
                all_articles.append(article)

    # 1. Fetch the known-good providers first, in priority order.
    # GDELT is free and always available — put it first as primary source
    primary_sources = [
        (fetch_gdelt_feed, "GDELT"),
    ]
    if settings.newsapi_key:
        primary_sources.append((fetch_newsapi_feed, "NewsAPI"))
    if settings.newsdata_api_key:
        primary_sources.append((fetch_newsdata_feed, "NewsData"))
    if settings.newscatcher_api_key:
        primary_sources.append((fetch_newscatcher_feed, "Newscatcher"))

    provider_semaphore = asyncio.Semaphore(max(1, min(settings.provider_concurrency, 5)))

    async def run_provider(fetch_func, source_name: str):
        async with provider_semaphore:
            breaker = SOURCE_BREAKERS.get(source_name)
            if breaker is None:
                breaker = CircuitState(source_name)
                SOURCE_BREAKERS[source_name] = breaker
            import time as _time
            _start = _time.monotonic()
            try:
                result = await fetch_func(client, country_code, country_name, source_limit, breaker)
                _dur = (_time.monotonic() - _start) * 1000
                try:
                    from backend.app.services.circuit_breaker import health_monitor
                    health_monitor.record_request(source_name, _dur, success=True)
                except Exception:
                    pass
                return result
            except Exception as exc:
                _dur = (_time.monotonic() - _start) * 1000
                try:
                    from backend.app.services.circuit_breaker import health_monitor
                    health_monitor.record_request(source_name, _dur, success=False)
                    health_monitor.record_error(source_name, str(exc)[:200])
                except Exception:
                    pass
                raise
        
    if primary_sources:
        primary_tasks = [
            run_provider(fetch_func, name)
            for fetch_func, name in primary_sources
        ]
        results = await asyncio.gather(*primary_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                add_unique_articles(result)

    # If we got enough articles from primary sources, return them immediately! (Very fast)
    if len(all_articles) >= limit:
         return all_articles[:limit]

    # 2. Fetch Fallback Sources (including NewsAPI) only if primary sources did not yield enough articles
    fallback_sources = [
        (fetch_freenewsapi_feed, "FreeNewsApi"),
        (fetch_newsapi_feed, "NewsAPI"),
        (fetch_gnews_feed, "GNews"),
        (fetch_worldnews_feed, "WorldNewsAPI"),
        (fetch_finnhub_feed, "Finnhub"),
        (fetch_currents_feed, "Currents"),
        (fetch_thenews_feed, "TheNews"),
        (fetch_mediastack_feed, "Mediastack"),
        (fetch_bing_news_feed, "BingNews"),
    ]
    
    fallback_tasks = [
        run_provider(fetch_func, name)
        for fetch_func, name in fallback_sources
    ]
    
    if fallback_tasks:
        results = await asyncio.gather(*fallback_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                add_unique_articles(result)

    # Score and rank articles by quality before returning
    scored = []
    now = datetime.now(timezone.utc)
    from backend.app.services.credibility import compute_source_reputation_score
    for article in all_articles:
        score = 0.0
        # Source reputation (0-1)
        score += compute_source_reputation_score(article.get("source", "")) * 0.3
        # Recency boost: fresher = higher score (0-0.4)
        pub = article.get("published_at")
        if pub:
            age_hours = max(0, (now - pub).total_seconds() / 3600)
            if age_hours < 6:
                score += 0.4
            elif age_hours < 24:
                score += 0.3
            elif age_hours < 72:
                score += 0.15
            else:
                score += 0.05
        # Content length bonus (0-0.2) — longer content = more detail
        content_len = len(article.get("content") or article.get("summary") or "")
        score += min(0.2, content_len / 1000 * 0.2)
        # Country name in title bonus (0-0.1)
        title_lower = (article.get("title") or "").lower()
        if country_name.lower() in title_lower:
            score += 0.1
        article["_relevance_score"] = round(score, 3)
        scored.append(article)

    scored.sort(key=lambda a: a["_relevance_score"], reverse=True)
    return scored[:limit]


async def fetch_rss_feed(client: httpx.AsyncClient, country_code: str, country_name: str, budget: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    # Use the same improved search queries as the API providers
    query = _search_query(country_name)


    url = f"https://news.google.com/rss/search?q={encode_query(query)}&hl=en-US&gl=US&ceid=US:en"
    response = await _get_with_retry(client, url, source_name="RSS", breaker=breaker)
    if not response or response.status_code != 200:
        return []
    
    items = _parse_rss_items(response.text, country_code, country_name, budget)
    
    async def resolve_item_url(item: Dict[str, Any]):
        orig_link = item.get("url")
        if orig_link and "news.google.com" in orig_link:
            try:
                from googlenewsdecoder import gnewsdecoder
                res = await asyncio.wait_for(asyncio.to_thread(gnewsdecoder, orig_link), timeout=2.0)
                if res.get("status") and res.get("decoded_url"):
                    item["url"] = res["decoded_url"]
                    item["source"] = get_clear_source(res["decoded_url"], item["source"])
            except Exception:
                pass

    if items:
        await asyncio.gather(*(resolve_item_url(item) for item in items), return_exceptions=True)
        
    return items


async def fetch_country_news(client: httpx.AsyncClient, country_code: str, budget: Optional[int] = None) -> List[Dict[str, Any]]:
    country_name = ISO_COUNTRIES.get(country_code, f"Country_{country_code}")
    priority = _country_priority(country_code)
    per_country_budget = budget or settings.scrape_limit_per_country
    if priority == "medium":
        per_country_budget = max(1, per_country_budget // settings.medium_priority_refresh_divisor)
    if priority == "low":
        per_country_budget = min(per_country_budget, 5)

    collected = await _fetch_all_news_sources(client, country_code, country_name, per_country_budget)
    seen_urls: set[str] = {article["url"] for article in collected if article.get("url")}

    if len(collected) < per_country_budget:
        rss_breaker = CircuitState("RSS")
        rss_articles = await fetch_rss_feed(client, country_code, country_name, per_country_budget - len(collected), rss_breaker)
        for article in rss_articles:
            if article["url"] and article["url"] not in seen_urls:
                collected.append(article)
                seen_urls.add(article["url"])

    return collected[:per_country_budget]


def _country_scan_order(test_mode: bool) -> List[str]:
    if test_mode:
        return list(dict.fromkeys(settings.critical_countries + settings.high_countries))
    return _priority_codes()


async def fetch_global_news(limit_per_country: int = 5, test_mode: bool = True) -> List[Dict[str, Any]]:
    countries_to_process = _country_scan_order(test_mode)
    logger.info("[Ingestion] Commencing ingestion cycle for %s countries.", len(countries_to_process))

    sem = asyncio.Semaphore(settings.request_concurrency)

    async def sem_fetch(client: httpx.AsyncClient, country_code: str):
        async with sem:
            articles = await fetch_country_news(client, country_code, budget=limit_per_country)
            return country_code, articles

    limits = httpx.Limits(max_keepalive_connections=settings.request_concurrency, max_connections=settings.request_concurrency * 2)
    async with httpx.AsyncClient(limits=limits, follow_redirects=True) as client:
        tasks = [sem_fetch(client, cc) for cc in countries_to_process]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles: List[Dict[str, Any]] = []
    for result in results:
        if isinstance(result, tuple):
            _, articles = result
            all_articles.extend(articles)
    logger.info("[Ingestion] Completed cycle. Retrieved %s raw articles.", len(all_articles))
    return all_articles
