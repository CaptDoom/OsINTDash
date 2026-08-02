import unittest
import sys
import os
from datetime import datetime

# Set sys.path to workspace root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from fastapi.testclient import TestClient
from sqlalchemy import select, func

from backend.app.main import app
from backend.app.database import get_db, Article, init_db_engine, create_tables
from backend.app.services.classifier import ImpactClassifier
from backend.app.services.ingestion import _parse_datetime, _country_priority
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
            published_at=datetime.utcnow(),
            impact_level="High Impact",
            department="Military & Defense"
        )
        summary = _local_summary([article], "24h")
        self.assertIn("Executive OSINT Briefing", summary)
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

    def test_chat_query_route(self):
        response = self.client.post("/api/chat/query", json={"query": "test"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("summary", data)
        self.assertIn("relevant_articles", data)

if __name__ == "__main__":
    unittest.main()
