from __future__ import annotations

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

for char1 in range(65, 91):
    for char2 in range(65, 91):
        code = chr(char1) + chr(char2)
        ISO_COUNTRIES.setdefault(code, f"Country_{code}")


@dataclass
class CircuitState:
    failures: int = 0
    open_until: float = 0.0

    def allow(self) -> bool:
        return asyncio.get_event_loop().time() >= self.open_until

    def record_failure(self, cooldown_seconds: float) -> None:
        self.failures += 1
        if self.failures >= 3:
            self.open_until = asyncio.get_event_loop().time() + cooldown_seconds

    def reset(self) -> None:
        self.failures = 0
        self.open_until = 0.0


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
    return list(dict.fromkeys(settings.critical_countries + settings.high_countries + settings.medium_countries + settings.low_countries))


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
            source = item.findtext("source") or "RSS"
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
    return {
        "title": title,
        "headline": title,
        "content": article.get("excerpt") or article.get("content") or article.get("title") or "",
        "summary": article.get("excerpt") or article.get("title") or "",
        "url": article.get("url") or "",
        "source": article.get("sourceCountry") or article.get("domain") or "GDELT",
        "country_code": country_code,
        "country_name": country_name,
        "published_at": _parse_datetime(article.get("seendate") or article.get("datetime")),
    }


async def _get_with_retry(client: httpx.AsyncClient, url: str, *, source_name: str, breaker: CircuitState, params: Optional[dict] = None, headers: Optional[dict] = None) -> Optional[httpx.Response]:
    if not breaker.allow():
        return None

    delay = settings.request_backoff_base_seconds
    for attempt in range(1, settings.request_retry_count + 1):
        try:
            response = await client.get(url, params=params, headers=headers, timeout=settings.request_timeout_seconds)
            if response.status_code == 429 or 500 <= response.status_code < 600:
                raise httpx.HTTPStatusError("retryable status", request=response.request, response=response)
            breaker.reset()
            return response
        except Exception as exc:
            logger.info("[Ingestion] %s request failed (attempt %s/%s): %s", source_name, attempt, settings.request_retry_count, exc)
            breaker.record_failure(settings.request_backoff_max_seconds)
            if attempt == settings.request_retry_count:
                return None
            await asyncio.sleep(min(delay, settings.request_backoff_max_seconds))
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
    return f"{country_name} conflict OR security OR border OR military OR diplomatic OR economy OR summit OR trade"


async def fetch_newsapi_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.newsapi_key:
        return []

    params = {
        "q": _search_query(country_name),
        "language": "en",
        "pageSize": limit,
        "sortBy": "relevancy",
        "apiKey": settings.newsapi_key,
    }
    response = await _get_with_retry(client, "https://newsapi.org/v2/everything", source_name="NewsAPI", breaker=breaker, params=params)
    if not response or response.status_code != 200:
        return []

    data = response.json()
    return [
        _normalize_api_article(article, country_code, country_name, article.get("source", {}).get("name", "NewsAPI"))
        for article in data.get("articles", [])[:limit]
    ]


async def fetch_gnews_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.gnews_api_key:
        return []

    params = {
        "q": _search_query(country_name),
        "lang": "en",
        "max": limit,
        "token": settings.gnews_api_key,
    }
    response = await _get_with_retry(client, "https://gnews.io/api/v4/search", source_name="GNews", breaker=breaker, params=params)
    if not response or response.status_code != 200:
        return []

    data = response.json()
    return [
        _normalize_api_article(article, country_code, country_name, article.get("source", "GNews"))
        for article in data.get("articles", [])[:limit]
    ]


async def fetch_newsdata_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.newsdata_api_key:
        return []

    params = {
        "apikey": settings.newsdata_api_key,
        "q": _search_query(country_name),
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

    params = {
        "key": settings.world_news_api_key,
        "language": "en",
        "q": _search_query(country_name),
        "page": 1,
        "pageSize": limit,
    }
    urls = [
        "https://www.worldnewsapi.com/search-news",
        "https://api.worldnewsapi.com/search-news",
    ]

    for url in urls:
        response = await _get_with_retry(client, url, source_name="WorldNewsAPI", breaker=breaker, params=params)
        if not response or response.status_code != 200:
            continue

        data = response.json()
        articles = data.get("articles") or data.get("data") or data.get("results") or []
        if not isinstance(articles, list):
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

    params = {
        "apiKey": settings.currents_api_key,
        "keywords": _search_query(country_name),
        "language": "en",
        "limit": limit,
    }
    response = await _get_with_retry(client, "https://api.currentsapi.services/v1/search", source_name="Currents", breaker=breaker, params=params)
    if not response or response.status_code != 200:
        return []

    data = response.json()
    return [
        _normalize_api_article(article, country_code, country_name, article.get("source", "Currents"))
        for article in data.get("news", [])[:limit]
    ]


async def fetch_thenews_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.thenews_api_key:
        return []

    params = {
        "api_token": settings.thenews_api_key,
        "language": "en",
        "search": _search_query(country_name),
        "limit": limit,
    }
    response = await _get_with_retry(client, "https://api.thenewsapi.com/v1/news/all", source_name="TheNews", breaker=breaker, params=params)
    if not response or response.status_code != 200:
        return []

    data = response.json()
    return [
        _normalize_api_article(article, country_code, country_name, article.get("source", "TheNews"))
        for article in data.get("data", [])[:limit]
    ]


async def fetch_mediastack_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.mediastack_api_key:
        return []

    params = {
        "access_key": settings.mediastack_api_key,
        "keywords": _search_query(country_name),
        "languages": "en",
        "limit": limit,
    }
    response = await _get_with_retry(client, "http://api.mediastack.com/v1/news", source_name="Mediastack", breaker=breaker, params=params)
    if not response or response.status_code != 200:
        return []

    data = response.json()
    return [
        _normalize_api_article(article, country_code, country_name, article.get("source", "Mediastack"))
        for article in data.get("data", [])[:limit]
    ]


async def fetch_newscatcher_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.newscatcher_api_key:
        return []

    headers = {"x-api-key": settings.newscatcher_api_key}
    params = {
        "q": _search_query(country_name),
        "lang": "en",
        "page_size": limit,
        "sort_by": "relevancy",
    }
    response = await _get_with_retry(client, "https://api.newscatcherapi.com/v2/search", source_name="Newscatcher", breaker=breaker, params=params, headers=headers)
    if not response or response.status_code != 200:
        return []

    data = response.json()
    return [
        _normalize_api_article(article, country_code, country_name, article.get("source", "Newscatcher"))
        for article in data.get("articles", [])[:limit]
    ]


async def fetch_bing_news_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    if not settings.bing_news_api_key:
        return []

    headers = {"Ocp-Apim-Subscription-Key": settings.bing_news_api_key}
    params = {
        "q": _search_query(country_name),
        "count": limit,
        "freshness": "Day",
        "textFormat": "Raw",
        "mkt": "en-US",
    }
    response = await _get_with_retry(client, "https://api.bing.microsoft.com/v7.0/news/search", source_name="BingNews", breaker=breaker, params=params, headers=headers)
    if not response or response.status_code != 200:
        return []

    data = response.json()
    return [
        _normalize_api_article(article, country_code, country_name, article.get("provider", [{}])[0].get("name", "BingNews"))
        for article in data.get("value", [])[:limit]
    ]


async def _fetch_all_news_sources(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int) -> List[Dict[str, Any]]:
    source_limit = max(limit, 10)
    sources = [
        fetch_newsapi_feed,
        fetch_gnews_feed,
        fetch_newsdata_feed,
        fetch_worldnews_feed,
        fetch_finnhub_feed,
        fetch_currents_feed,
        fetch_thenews_feed,
        fetch_mediastack_feed,
        fetch_newscatcher_feed,
        fetch_bing_news_feed,
    ]
    tasks = [
        fetch(client, country_code, country_name, source_limit, CircuitState())
        for fetch in sources
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_articles: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    for result in results:
        if isinstance(result, list):
            for article in result:
                if not article["url"]:
                    title_hash = hashlib.sha256(article["title"].encode("utf-8")).hexdigest()
                    if title_hash in seen_titles:
                        continue
                    seen_titles.add(title_hash)
                elif article["url"] in seen_urls:
                    continue
                else:
                    seen_urls.add(article["url"])

                all_articles.append(article)
                if len(all_articles) >= limit:
                    return all_articles

    return all_articles


async def fetch_rss_feed(client: httpx.AsyncClient, country_code: str, country_name: str, budget: int, breaker: CircuitState) -> List[Dict[str, Any]]:
    query = f"{country_name} border OR {country_name} security OR {country_name} conflict"
    if country_code in {"BT", "MV", "LK", "NP"}:
        query = f"{country_name} geopolitical OR {country_name} relations OR {country_name}"
    if _country_priority(country_code) in {"medium", "low"}:
        query = f"{country_name} news OR {country_name} update OR {country_name} politics"

    url = f"https://news.google.com/rss/search?q={encode_query(query)}&hl=en-US&gl=US&ceid=US:en"
    response = await _get_with_retry(client, url, source_name="RSS", breaker=breaker)
    if not response or response.status_code != 200:
        return []
    return _parse_rss_items(response.text, country_code, country_name, budget)


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
        rss_breaker = CircuitState()
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
