import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Dict, Any, Optional
import httpx
from backend.app.config import settings

logger = logging.getLogger("drishya.ingestion")

# ISO 3166-1 alpha-2 mapping to country names for keyword queries
ISO_COUNTRIES = {
    "IN": "India", "CN": "China", "PK": "Pakistan", "AF": "Afghanistan", 
    "BD": "Bangladesh", "MM": "Myanmar", "NP": "Nepal", "BT": "Bhutan", 
    "LK": "Sri Lanka", "MV": "Maldives", "US": "United States", "RU": "Russia", 
    "UA": "Ukraine", "IR": "Iran", "IL": "Israel", "TW": "Taiwan", "JP": "Japan",
    "GB": "United Kingdom", "FR": "France", "DE": "Germany", "KR": "South Korea"
}

# Expand with all other major country codes to support 190+
for char1 in range(65, 91): # A-Z
    for char2 in range(65, 91): # A-Z
        code = chr(char1) + chr(char2)
        if code not in ISO_COUNTRIES:
            ISO_COUNTRIES[code] = f"Country_{code}"

async def fetch_rss_feed(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int) -> List[Dict[str, Any]]:
    # Google News RSS Search URL
    query = f"{country_name} border OR {country_name} security OR {country_name} conflict"
    if country_code in ["BT", "MV", "LK", "NP"]:
        # Broaden query for micro-states / island nations to ensure we get articles
        query = f"{country_name} geopolitical OR {country_name} relations OR {country_name}"

    url = f"https://news.google.com/rss/search?q={encode_query(query)}&hl=en-US&gl=US&ceid=US:en"
    
    articles = []
    try:
        response = await client.get(url, timeout=10.0)
        if response.status_code != 200:
            return []
        
        root = ET.fromstring(response.text)
        channel = root.find("channel")
        if channel is None:
            return []
        
        items = channel.findall("item")[:limit]
        for item in items:
            title = item.find("title").text if item.find("title") is not None else "Untitled"
            link = item.find("link").text if item.find("link") is not None else ""
            desc = item.find("description").text if item.find("description") is not None else ""
            pub_date_str = item.find("pubDate").text if item.find("pubDate") is not None else ""
            source = item.find("source").text if item.find("source") is not None else "RSS Fallback"
            
            # Clean HTML tags from description if present
            desc_clean = desc
            if "<" in desc_clean:
                # Basic strip
                import re
                desc_clean = re.sub('<[^<]+?>', '', desc_clean)
                
            published_at = datetime.utcnow()
            if pub_date_str:
                try:
                    # Parse standard RSS pubDate e.g. "Fri, 31 Jul 2026 14:20:00 GMT"
                    published_at = datetime.strptime(pub_date_str[:25].strip(), "%a, %d %b %Y %H:%M:%S")
                except Exception:
                    pass
            
            articles.append({
                "title": title,
                "headline": title,
                "content": desc_clean if desc_clean else title,
                "url": link,
                "source": source,
                "country_code": country_code,
                "published_at": published_at
            })
    except Exception as e:
        logger.warning(f"[Ingestion] RSS scrape failed for {country_name} ({country_code}): {e}")
        
    return articles

async def fetch_news_api(client: httpx.AsyncClient, country_code: str, country_name: str, limit: int) -> List[Dict[str, Any]]:
    if not settings.NEWSAPI_KEY:
        return []
        
    # Translate ISO code to lowercase
    cc_lower = country_code.lower()
    url = f"https://newsapi.org/v2/top-headlines?country={cc_lower}&pageSize={limit}&apiKey={settings.NEWSAPI_KEY}"
    
    # Fallback to keywords search if country is not supported by top-headlines
    unsupported_countries = ["AF", "BT", "MV", "NP", "MM"]
    if country_code in unsupported_countries:
        url = f"https://newsapi.org/v2/everything?q={country_name}&pageSize={limit}&sortBy=publishedAt&apiKey={settings.NEWSAPI_KEY}"

    try:
        response = await client.get(url, timeout=10.0)
        if response.status_code != 200:
            return []
        
        data = response.json()
        articles = []
        for art in data.get("articles", [])[:limit]:
            published_at = datetime.utcnow()
            if art.get("publishedAt"):
                try:
                    published_at = datetime.fromisoformat(art["publishedAt"].replace("Z", "+00:00"))
                except Exception:
                    pass
                    
            articles.append({
                "title": art.get("title", "Untitled"),
                "headline": art.get("title", "Untitled"),
                "content": art.get("description") or art.get("content") or art.get("title") or "",
                "url": art.get("url", ""),
                "source": art.get("source", {}).get("name") or "NewsAPI",
                "country_code": country_code,
                "published_at": published_at
            })
        return articles
    except Exception as e:
        logger.warning(f"[Ingestion] NewsAPI scrape failed for {country_name}: {e}")
        return []

def encode_query(query: str) -> str:
    import urllib.parse
    return urllib.parse.quote_plus(query)

async def fetch_country_news(client: httpx.AsyncClient, country_code: str, limit: int = 5) -> List[Dict[str, Any]]:
    country_name = ISO_COUNTRIES.get(country_code, f"Country_{country_code}")
    
    # 1. Try NewsAPI (if key configured)
    articles = await fetch_news_api(client, country_code, country_name, limit)
    
    # 2. Fallback/Alternative: Google News RSS
    if not articles or len(articles) < limit:
        rss_articles = await fetch_rss_feed(client, country_code, country_name, limit - len(articles))
        articles.extend(rss_articles)
        
    return articles[:limit]

async def fetch_global_news(limit_per_country: int = 5, test_mode: bool = True) -> List[Dict[str, Any]]:
    """
    Asynchronously queries global feeds.
    In test_mode, only processes border countries to protect rate limits and speeds.
    """
    countries_to_process = ["CN", "PK", "AF", "BD", "MM", "NP", "BT", "LK", "MV"] # Border focus
    
    if not test_mode:
        # Full list of 190+ countries
        countries_to_process = list(ISO_COUNTRIES.keys())
        
    logger.info(f"[Ingestion] Commencing ingestion cycle for {len(countries_to_process)} countries.")
    
    # Restrict concurrency to avoid getting IP-blocked
    sem = asyncio.Semaphore(5)
    
    async def sem_fetch(client: httpx.AsyncClient, cc: str):
        async with sem:
            return await fetch_country_news(client, cc, limit_per_country)
            
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    async with httpx.AsyncClient(limits=limits, follow_redirects=True) as client:
        tasks = [sem_fetch(client, cc) for cc in countries_to_process]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
    all_articles = []
    for res in results:
        if isinstance(res, list):
            all_articles.extend(res)
            
    logger.info(f"[Ingestion] Completed cycle. Retrieved {len(all_articles)} raw articles.")
    return all_articles
