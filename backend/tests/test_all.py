import unittest
import sys
import os
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

# Set sys.path to workspace root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ["TESTING"] = "1"

from fastapi.testclient import TestClient
from sqlalchemy import select, func

from backend.app.main import app
from backend.app.database import get_db, Article, init_db_engine, create_tables
from backend.app.services.classifier import ImpactClassifier
from backend.app.services.ingestion import _parse_datetime, _country_priority
from backend.app.services import ingestion
from backend.app.services.summarizer import _local_summary

class TestDatabase(unittest.IsolatedAsyncioTestCase):
    async def test_db_initialization(self):
        # Verify database initialization and tables creation
        await create_tables()
        async for db in get_db():
            # Verify we can execute queries on the database
            res = await db.execute(select(func.count(Article.id)))
            count = res.scalar()
            self.assertIsNotNone(count)
            break

class TestClassifier(unittest.TestCase):
    def setUp(self):
        self.classifier = ImpactClassifier()

    def test_high_impact_classification(self):
        # Lowered threshold requires at least 1 keyword
        title = "Urgent: Missile launch detected near LOC"
        content = "Tactical command reports troop movements along border sector."
        impact, dept = self.classifier.classify(title, content)
        self.assertEqual(impact, "High Impact")
        self.assertEqual(dept, "Military & Defense")

    def test_medium_impact_classification(self):
        title = "Bilateral trade agreement signed"
        content = "Ministers complete summits on tariff reductions and border crossing logistics."
        impact, dept = self.classifier.classify(title, content)
        self.assertEqual(impact, "Medium Impact")
        self.assertEqual(dept, "Political & Diplomatic")

    def test_normal_impact_classification(self):
        title = "National cricket team wins championship"
        content = "Fans celebrate sports victory in annual festival quiz match."
        impact, dept = self.classifier.classify(title, content)
        self.assertEqual(impact, "Normal Impact")

    def test_fuzzy_title_similarity_logic(self):
        import difflib
        title1 = "Troop movement detected near Tawang Sector"
        title2 = "Troop movements detected near Tawang Sectors"
        ratio = difflib.SequenceMatcher(None, title1.lower(), title2.lower()).ratio()
        self.assertTrue(ratio > 0.80)

class TestIngestionHelpers(unittest.TestCase):
    def test_parse_datetime(self):
        # Valid ISO datetime
        dt = _parse_datetime("2026-08-02T12:00:00Z")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 8)
        self.assertEqual(dt.day, 2)
        
        # Fallback for invalid datetime
        dt_fallback = _parse_datetime("invalid-date-string")
        self.assertIsInstance(dt_fallback, datetime)

    def test_country_priority(self):
        self.assertEqual(_country_priority("CN"), "critical")
        self.assertEqual(_country_priority("PK"), "critical")
        self.assertEqual(_country_priority("BD"), "high")
        self.assertEqual(_country_priority("US"), "medium")


class TestNewsIngestion(unittest.IsolatedAsyncioTestCase):
    async def test_auth_failure_trips_without_retrying(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def get(self, *args, **kwargs):
                self.calls += 1
                return type("Response", (), {"status_code": 401})()

        client = FakeClient()
        breaker = ingestion.CircuitState("NewsAPI")
        with patch.object(ingestion, "notify_circuit_breaker", new=AsyncMock()):
            response = await ingestion._get_with_retry(
                client, "https://news.example", source_name="NewsAPI", breaker=breaker
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(client.calls, 1)
        self.assertFalse(await breaker.allow())

    async def test_rate_limit_uses_tolerance_counter(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def get(self, *args, **kwargs):
                self.calls += 1
                return type("Response", (), {"status_code": 429})()

        client = FakeClient()
        breaker = ingestion.CircuitState("Mediastack")
        with patch.object(ingestion, "notify_circuit_breaker", new=AsyncMock()), \
             patch.object(ingestion.asyncio, "sleep", new=AsyncMock()):
            response = await ingestion._get_with_retry(
                client, "https://news.example", source_name="Mediastack", breaker=breaker
            )

        self.assertIsNone(response)
        self.assertEqual(client.calls, 3)
        self.assertEqual(breaker._rate_limit_failures, 1)
        self.assertTrue(await breaker.allow())

    async def test_server_error_stops_after_two_attempts(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def get(self, *args, **kwargs):
                self.calls += 1
                return type("Response", (), {"status_code": 500})()

        client = FakeClient()
        breaker = ingestion.CircuitState("NewsAPI")
        with patch.object(ingestion, "notify_circuit_breaker", new=AsyncMock()), \
             patch.object(ingestion.asyncio, "sleep", new=AsyncMock()):
            response = await ingestion._get_with_retry(
                client, "https://news.example", source_name="NewsAPI", breaker=breaker
            )

        self.assertIsNone(response)
        self.assertEqual(client.calls, 2)
        self.assertEqual(await breaker.get_failures(), 2)

    async def test_provider_results_are_deduplicated_and_limited(self):
        long_content = " ".join(f"word{i}" for i in range(30))  # varied words to pass quality checks
        now = datetime.now(timezone.utc)
        duplicate_a = {
            "title": "Border radar deployment near Tawang",
            "url": "https://news.example/a",
            "content": long_content,
            "published_at": now,
        }
        duplicate_b = {
            "title": "Border radar deployment near Tawang sector",
            "url": "https://news.example/b",
            "content": long_content,
            "published_at": now,
        }
        unique = {
            "title": "Trade corridor talks resume",
            "url": "https://news.example/c",
            "content": " ".join(f"unique{i}topic" for i in range(30)),
            "published_at": now,
        }

        async def provider(_client, _code, _name, _limit, breaker):
            self.assertTrue(breaker.source_name)
            return [duplicate_a, duplicate_b, unique]

        provider_names = [
            "fetch_freenewsapi_feed", "fetch_newsapi_feed", "fetch_gnews_feed",
            "fetch_worldnews_feed", "fetch_finnhub_feed", "fetch_currents_feed",
            "fetch_thenews_feed", "fetch_mediastack_feed", "fetch_bing_news_feed",
        ]
        with patch.multiple(ingestion, **{name: provider for name in provider_names}), \
             patch.object(ingestion.settings, "newscatcher_api_key", None), \
             patch.object(ingestion.settings, "newsdata_api_key", None):
            articles = await ingestion._fetch_all_news_sources(None, "CN", "China", 2)

        self.assertEqual(len(articles), 2)
        self.assertEqual(
            [article["url"] for article in articles],
            ["https://news.example/a", "https://news.example/c"],
        )

    async def test_country_news_constructs_named_rss_breaker(self):
        rss_article = {"title": "RSS article", "url": "https://news.example/rss"}
        with patch.object(ingestion, "_fetch_all_news_sources", new=AsyncMock(return_value=[])), \
             patch.object(ingestion, "fetch_rss_feed", new=AsyncMock(return_value=[rss_article])) as rss_mock:
            articles = await ingestion.fetch_country_news(None, "CN", budget=1)

        self.assertEqual(articles, [rss_article])
        self.assertEqual(rss_mock.await_args.args[4].source_name, "RSS")

class TestSummarizerFallback(unittest.TestCase):
    def test_heuristic_summary_generation(self):
        article = Article(
            id="test-id",
            title="Border Radar Sweeps",
            headline="Border Radar Sweeps",
            content="Radar scanning arrays verified along LOC.",
            url="https://intel.local/test",
            source="TEST-SOURCE",
            country_code="CN",
            published_at=datetime.now(timezone.utc),
            impact_level="High Impact",
            department="Military & Defense"
        )
        summary = _local_summary([article], "24h")
        self.assertIn("News Briefing", summary)
        self.assertIn("Military & Defense", summary)
        self.assertIn("Border Radar Sweeps", summary)

class TestIntentDetection(unittest.TestCase):
    """Unit tests for the chat intent detection function."""

    def test_risk_assessment_detection(self):
        from backend.app.api.routes.chat import detect_query_intent
        intent = detect_query_intent("What is the risk level in Pakistan?")
        self.assertEqual(intent["type"], "risk_assessment")
        self.assertEqual(intent["country"], "PK")

    def test_trend_analysis_detection(self):
        from backend.app.api.routes.chat import detect_query_intent
        intent = detect_query_intent("How are things trending in China this week?")
        self.assertEqual(intent["type"], "trend_analysis")
        self.assertEqual(intent["country"], "CN")
        self.assertEqual(intent["timeframe"], "7d")

    def test_briefing_detection(self):
        from backend.app.api.routes.chat import detect_query_intent
        intent = detect_query_intent("Give me a summary of Myanmar")
        self.assertEqual(intent["type"], "briefing")
        self.assertEqual(intent["country"], "MM")

    def test_source_verification_detection(self):
        from backend.app.api.routes.chat import detect_query_intent
        intent = detect_query_intent("Is the source about Russia credible?")
        self.assertEqual(intent["type"], "source_verification")
        self.assertEqual(intent["country"], "RU")

    def test_forecast_detection(self):
        from backend.app.api.routes.chat import detect_query_intent
        intent = detect_query_intent("What is the outlook for Ukraine next month?")
        self.assertEqual(intent["type"], "forecast")
        self.assertEqual(intent["country"], "UA")

    def test_department_detection(self):
        from backend.app.api.routes.chat import detect_query_intent
        intent = detect_query_intent("Any cyber attacks in India?")
        self.assertEqual(intent["department"], "Technology & Cyber")
        self.assertEqual(intent["country"], "IN")

    def test_default_general_intent(self):
        from backend.app.api.routes.chat import detect_query_intent
        intent = detect_query_intent("hello")
        self.assertEqual(intent["type"], "general")
        self.assertIsNone(intent["country"])

    def test_timeframe_today(self):
        from backend.app.api.routes.chat import detect_query_intent
        intent = detect_query_intent("What is happening right now?")
        self.assertEqual(intent["timeframe"], "24h")


class TestFastAPIRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_check(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_metrics_endpoint(self):
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("http_requests_total", response.text)

    def test_news_all_route(self):
        response = self.client.get("/api/news/all")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("China", data)
        self.assertIn("Pakistan", data)
        for country, dossier in data.items():
            self.assertIsInstance(country, str)
            self.assertIn("signals", dossier)
            self.assertIsInstance(dossier["signals"], list)
            for signal in dossier["signals"]:
                for key in ["id", "headline", "summary", "source", "timestamp", "url", "impact"]:
                    self.assertIn(key, signal)
                self.assertIn(signal["impact"], ["High", "Medium", "Low"])
                self.assertRegex(signal["url"], r"^https?://")

    def test_news_country_contract_without_external_network(self):
        with patch("backend.app.main.fetch_and_classify_background", new=AsyncMock()):
            response = self.client.get("/api/news/country?name=Testland&code=ZZ")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["source_status"], "normal")
        self.assertIsInstance(data["signals"], list)
        self.assertIn("threat_level", data)

    def test_world_alerts_contract(self):
        response = self.client.get("/api/world/alerts?force=true")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], len(data["alerts"]))
        for alert in data["alerts"]:
            for key in ["id", "location", "lat", "lon", "severity", "headline", "url", "timestamp"]:
                self.assertIn(key, alert)
            self.assertIn(alert["severity"], ["high", "medium", "low"])

    def test_country_risk_contract(self):
        response = self.client.get("/api/risk/country?code=ZZ&days=30")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["country_code"], "ZZ")
        self.assertEqual(data["level"], "Low")
        self.assertEqual(data["article_count"], 0)
        self.assertIn(data["trend"], ["stable", "rising", "falling"])

    def test_archive_export_contract(self):
        json_response = self.client.get("/api/archive/export/1M?format=json")
        self.assertEqual(json_response.status_code, 200)
        self.assertIsInstance(json_response.json(), list)

        csv_response = self.client.get("/api/archive/export/1M?format=csv")
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("text/csv", csv_response.headers["content-type"])
        self.assertIn("id,title", csv_response.text)

    def test_alert_rule_lifecycle(self):
        create_response = self.client.post(
            "/api/alerts/rules",
            json={"name": "Border escalation", "keywords": ["missile", "border"], "minimum_risk": "High"},
        )
        self.assertEqual(create_response.status_code, 200)
        rule = create_response.json()
        self.assertEqual(rule["keywords"], ["missile", "border"])

        list_response = self.client.get("/api/alerts/rules")
        self.assertEqual(list_response.status_code, 200)
        self.assertTrue(any(item["id"] == rule["id"] for item in list_response.json()))

        active_response = self.client.get("/api/alerts/active")
        self.assertEqual(active_response.status_code, 200)
        self.assertIn("alerts", active_response.json())

        delete_response = self.client.delete(f"/api/alerts/rules/{rule['id']}")
        self.assertEqual(delete_response.status_code, 200)

    def test_news_query_contract(self):
        response = self.client.post("/api/news/query", json={"query": "zzzz-no-match"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("summary", data)
        self.assertIn("sources", data)
        self.assertIn("matchedCount", data)
        self.assertIsInstance(data["sources"], list)
        self.assertEqual(data["matchedCount"], 0)

    def test_news_refresh_success_and_failure(self):
        with patch("backend.app.main.run_ingestion_cycle", new=AsyncMock(return_value={
            "raw_articles": 2, "processed": 2, "high_impact": 1,
        })) as refresh_mock:
            response = self.client.post("/api/news/refresh")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "success": True, "raw_articles": 2, "processed": 2, "high_impact": 1,
        })
        refresh_mock.assert_awaited_once_with(test_mode=False)

        with patch("backend.app.main.run_ingestion_cycle", new=AsyncMock(side_effect=RuntimeError("provider down"))):
            response = self.client.post("/api/news/refresh")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "News refresh failed")

    def test_news_all_max_age_hours(self):
        response = self.client.get("/api/news/all?max_age_hours=1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("China", data)

    def test_weather_border_route(self):
        response = self.client.get("/api/weather/border")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for name in ["Siachen Glacier", "Pangong Tso", "Tawang Sector", "Doklam Sector", "Sir Creek"]:
            self.assertIn(name, data)
            sector_data = data[name]
            self.assertEqual(sector_data["sector"], name)
            self.assertIn("temperature", sector_data)
            self.assertIn("condition", sector_data)
            self.assertIn("visibility_km", sector_data)
            self.assertIn("wind_speed_kmh", sector_data)
            self.assertIn("source", sector_data)

    def test_chat_query_route(self):
        response = self.client.post("/api/chat/query", json={"query": "test"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("summary", data)
        self.assertIn("relevant_articles", data)

    def test_custom_summarizer_route(self):
        response = self.client.post(
            "/api/summarizer/generate",
            data={"country_code": "CN", "timeframe": "1M"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("summary", data)

    def test_shared_notes_lifecycle(self):
        # 1. Create a note
        post_resp = self.client.post(
            "/api/notes",
            json={"content": "Strategic alert: LAC patrol checks completed.", "author": "CEO"}
        )
        self.assertEqual(post_resp.status_code, 200)
        note_data = post_resp.json()
        self.assertIn("id", note_data)
        self.assertEqual(note_data["author"], "CEO")
        self.assertEqual(note_data["content"], "Strategic alert: LAC patrol checks completed.")
        note_id = note_data["id"]

        # 2. Get active notes
        get_resp = self.client.get("/api/notes")
        self.assertEqual(get_resp.status_code, 200)
        notes_list = get_resp.json()
        self.assertTrue(any(n["id"] == note_id for n in notes_list))

        # 3. Update and verify version history
        update_resp = self.client.put(
            f"/api/notes/{note_id}",
            json={"content": "Strategic alert updated: patrol checks completed.", "author": "CEO"},
        )
        self.assertEqual(update_resp.status_code, 200)
        history_resp = self.client.get(f"/api/notes/{note_id}/history")
        self.assertEqual(history_resp.status_code, 200)
        self.assertGreaterEqual(len(history_resp.json()), 2)

        # 4. Delete the note
        del_resp = self.client.delete(f"/api/notes/{note_id}")
        self.assertEqual(del_resp.status_code, 200)
        self.assertEqual(del_resp.json(), {"status": "deleted"})

    # ─── Intelligence Dashboard ──────────────────────────────────────

    def test_intelligence_dashboard_empty(self):
        """Dashboard returns valid structure even with no articles."""
        response = self.client.get("/api/intelligence/dashboard?days=1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("period_days", data)
        self.assertIn("total_articles", data)
        self.assertIn("impact_breakdown", data)
        self.assertIn("by_department", data)
        self.assertIn("top_countries", data)
        self.assertIn("trend", data)
        self.assertIn("source_health", data)
        self.assertIn("top_stories", data)
        self.assertIn("generated_at", data)
        self.assertEqual(data["period_days"], 1)

    def test_intelligence_dashboard_trend_structure(self):
        """Trend data has correct nested structure."""
        response = self.client.get("/api/intelligence/dashboard?days=30")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        trend = data["trend"]
        self.assertIn("direction", trend)
        self.assertIn(trend["direction"], ["rising", "stable", "falling"])
        self.assertIn("daily", trend)
        self.assertIsInstance(trend["daily"], dict)

    def test_intelligence_dashboard_impact_breakdown(self):
        """Impact breakdown contains expected keys."""
        response = self.client.get("/api/intelligence/dashboard?days=7")
        self.assertEqual(response.status_code, 200)
        breakdown = response.json()["impact_breakdown"]
        self.assertIn("high", breakdown)
        self.assertIn("medium", breakdown)
        self.assertIn("normal", breakdown)
        self.assertIsInstance(breakdown["high"], int)

    def test_intelligence_dashboard_top_stories_structure(self):
        """Top stories have all required fields when present."""
        response = self.client.get("/api/intelligence/dashboard?days=30")
        self.assertEqual(response.status_code, 200)
        for story in response.json()["top_stories"]:
            for key in ["id", "title", "summary", "country", "department", "source", "url", "timestamp", "corroborated_by"]:
                self.assertIn(key, story)

    def test_intelligence_dashboard_source_health(self):
        """Source health entries have reputation data."""
        response = self.client.get("/api/intelligence/dashboard?days=30")
        self.assertEqual(response.status_code, 200)
        for source, info in response.json()["source_health"].items():
            self.assertIn("article_count", info)
            self.assertIn("high_impact_ratio", info)
            self.assertIn("reputation_score", info)
            self.assertIn("tier", info)
            self.assertIsInstance(info["reputation_score"], float)
            self.assertGreaterEqual(info["reputation_score"], 0.0)
            self.assertLessEqual(info["reputation_score"], 1.0)

    # ─── Chat Streaming ──────────────────────────────────────────────

    def test_chat_stream_returns_sse(self):
        """Streaming chat endpoint returns SSE with articles and done events."""
        response = self.client.post("/api/chat/stream", json={"query": "test"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        body = response.text
        # Must have articles event and done event
        self.assertIn("event: articles", body)
        self.assertIn("event: token", body)
        self.assertIn("event: done", body)

    def test_chat_stream_empty_query_rejected(self):
        """Empty query should return 400."""
        response = self.client.post("/api/chat/stream", json={"query": ""})
        self.assertEqual(response.status_code, 400)

    def test_chat_stream_articles_contain_intent(self):
        """Articles event includes detected intent metadata."""
        response = self.client.post("/api/chat/stream", json={"query": "risk in Pakistan"})
        self.assertEqual(response.status_code, 200)
        body = response.text
        # Extract the articles event data
        import re, json
        match = re.search(r"event: articles\ndata: ({.*?})\n\n", body)
        self.assertIsNotNone(match)
        event_data = json.loads(match.group(1))
        self.assertIn("articles", event_data)
        self.assertIn("intent", event_data)
        self.assertEqual(event_data["intent"]["country"], "PK")
        self.assertEqual(event_data["intent"]["type"], "risk_assessment")

    def test_chat_stream_done_event_has_summary(self):
        """Done event contains summary and relevant_articles."""
        response = self.client.post("/api/chat/stream", json={"query": "military news"})
        self.assertEqual(response.status_code, 200)
        body = response.text
        import re, json
        match = re.search(r"event: done\ndata: ({.*?})\n\n", body)
        self.assertIsNotNone(match)
        done_data = json.loads(match.group(1))
        self.assertIn("summary", done_data)
        self.assertIn("relevant_articles", done_data)
        self.assertIsInstance(done_data["relevant_articles"], list)

    # ─── Summarizer Streaming ────────────────────────────────────────

    def test_summarizer_stream_returns_sse(self):
        """Streaming summarizer returns SSE with metadata and token events."""
        response = self.client.post(
            "/api/summarizer/stream",
            data={"country_code": "CN", "timeframe": "1M"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        body = response.text
        self.assertIn("event: metadata", body)
        self.assertIn("event: token", body)
        self.assertIn("event: done", body)

    def test_summarizer_stream_metadata_structure(self):
        """Metadata event contains country stats and article list."""
        response = self.client.post(
            "/api/summarizer/stream",
            data={"country_code": "CN", "timeframe": "1M"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.text
        import re, json
        match = re.search(r"event: metadata\ndata: ({.*?})\n\n", body)
        self.assertIsNotNone(match)
        meta = json.loads(match.group(1))
        self.assertIn("country", meta)
        self.assertIn("timeframe", meta)
        self.assertIn("total_articles", meta)
        self.assertIn("high_impact", meta)
        self.assertIn("medium_impact", meta)
        self.assertIn("sources", meta)
        self.assertIn("articles", meta)
        self.assertEqual(meta["country"], "China")
        self.assertEqual(meta["timeframe"], "1 Month")

    def test_summarizer_stream_invalid_timeframe(self):
        """Invalid timeframe returns 400."""
        response = self.client.post(
            "/api/summarizer/stream",
            data={"country_code": "CN", "timeframe": "5Y"}
        )
        self.assertEqual(response.status_code, 400)

    def test_summarizer_stream_done_event(self):
        """Done event contains the full summary text."""
        response = self.client.post(
            "/api/summarizer/stream",
            data={"country_code": "CN", "timeframe": "1M"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.text
        import re, json
        match = re.search(r"event: done\ndata: ({.*?})\n\n", body)
        self.assertIsNotNone(match)
        done_data = json.loads(match.group(1))
        self.assertIn("summary", done_data)
        self.assertIsInstance(done_data["summary"], str)
        self.assertGreater(len(done_data["summary"]), 0)

if __name__ == "__main__":
    unittest.main()
