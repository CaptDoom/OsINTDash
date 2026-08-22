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
        duplicate_a = {
            "title": "Border radar deployment near Tawang",
            "url": "https://news.example/a",
        }
        duplicate_b = {
            "title": "Border radar deployment near Tawang sector",
            "url": "https://news.example/b",
        }
        unique = {"title": "Trade corridor talks resume", "url": "https://news.example/c"}

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

if __name__ == "__main__":
    unittest.main()
