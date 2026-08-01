from __future__ import annotations

import contextvars
import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get("-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "__dict__", {})
        for key in ("service", "event", "path", "method", "status_code", "duration_ms", "job_id", "job_type"):
            if key in extra and extra[key] is not None:
                payload[key] = extra[key]
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)


@dataclass
class MetricsState:
    http_requests_total: int = 0
    http_request_errors_total: int = 0
    http_request_duration_seconds_sum: float = 0.0
    ingestion_articles_total: int = 0
    ingestion_duplicates_total: int = 0
    classification_batches_total: int = 0
    archive_summary_requests_total: int = 0
    fusion_jobs_total: int = 0
    fusion_jobs_completed_total: int = 0
    fusion_jobs_failed_total: int = 0


class MetricsRegistry:
    def __init__(self) -> None:
        self.state = MetricsState()
        self._start = time.perf_counter()

    def render_prometheus(self) -> str:
        uptime = time.perf_counter() - self._start
        lines = [
            "# HELP drishya_uptime_seconds Service uptime in seconds",
            "# TYPE drishya_uptime_seconds gauge",
            f"drishya_uptime_seconds {uptime:.3f}",
            "# HELP drishya_http_requests_total Total HTTP requests",
            "# TYPE drishya_http_requests_total counter",
            f"drishya_http_requests_total {self.state.http_requests_total}",
            "# HELP drishya_http_request_errors_total Total HTTP errors",
            "# TYPE drishya_http_request_errors_total counter",
            f"drishya_http_request_errors_total {self.state.http_request_errors_total}",
            "# HELP drishya_http_request_duration_seconds_sum Total request duration",
            "# TYPE drishya_http_request_duration_seconds_sum counter",
            f"drishya_http_request_duration_seconds_sum {self.state.http_request_duration_seconds_sum:.6f}",
            "# HELP drishya_ingestion_articles_total Total ingested articles",
            "# TYPE drishya_ingestion_articles_total counter",
            f"drishya_ingestion_articles_total {self.state.ingestion_articles_total}",
            "# HELP drishya_ingestion_duplicates_total Duplicate ingestions skipped",
            "# TYPE drishya_ingestion_duplicates_total counter",
            f"drishya_ingestion_duplicates_total {self.state.ingestion_duplicates_total}",
            "# HELP drishya_classification_batches_total Classification batches processed",
            "# TYPE drishya_classification_batches_total counter",
            f"drishya_classification_batches_total {self.state.classification_batches_total}",
            "# HELP drishya_archive_summary_requests_total Summary requests",
            "# TYPE drishya_archive_summary_requests_total counter",
            f"drishya_archive_summary_requests_total {self.state.archive_summary_requests_total}",
            "# HELP drishya_fusion_jobs_total Fusion jobs created",
            "# TYPE drishya_fusion_jobs_total counter",
            f"drishya_fusion_jobs_total {self.state.fusion_jobs_total}",
            "# HELP drishya_fusion_jobs_completed_total Fusion jobs completed",
            "# TYPE drishya_fusion_jobs_completed_total counter",
            f"drishya_fusion_jobs_completed_total {self.state.fusion_jobs_completed_total}",
            "# HELP drishya_fusion_jobs_failed_total Fusion jobs failed",
            "# TYPE drishya_fusion_jobs_failed_total counter",
            f"drishya_fusion_jobs_failed_total {self.state.fusion_jobs_failed_total}",
        ]
        return "\n".join(lines) + "\n"


metrics = MetricsRegistry()
