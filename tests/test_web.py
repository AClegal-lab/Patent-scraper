"""Tests for the Flask web UI."""

import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from patent_monitor.db import Database
from patent_monitor.models import Patent
from patent_monitor.web.app import create_app
from patent_monitor.web.tasks import TaskInfo, TaskManager


# --- TaskManager tests ---


class TestTaskManager:
    def test_start_task(self):
        tm = TaskManager()
        task_id = tm.start_task("test", lambda progress_callback=None: "done")
        assert task_id is not None
        assert len(task_id) == 12

    def test_get_task(self):
        tm = TaskManager()
        task_id = tm.start_task("test", lambda progress_callback=None: "done")
        time.sleep(0.1)
        task = tm.get_task(task_id)
        assert task is not None
        assert task.name == "test"

    def test_task_completes(self):
        tm = TaskManager()
        task_id = tm.start_task("test", lambda progress_callback=None: {"result": 42})
        time.sleep(0.3)
        task = tm.get_task(task_id)
        assert task.status == "completed"
        assert task.result == {"result": 42}

    def test_task_fails(self):
        def failing_fn(progress_callback=None):
            raise ValueError("boom")

        tm = TaskManager()
        task_id = tm.start_task("test", failing_fn)
        time.sleep(0.3)
        task = tm.get_task(task_id)
        assert task.status == "failed"
        assert "boom" in task.error

    def test_progress_callback(self):
        def slow_fn(progress_callback=None):
            if progress_callback:
                progress_callback("step 1")
                progress_callback("step 2")
            return "done"

        tm = TaskManager()
        task_id = tm.start_task("test", slow_fn)
        time.sleep(0.3)
        task = tm.get_task(task_id)
        assert task.message == "step 2"  # Last progress message

    def test_has_running_task(self):
        import threading

        event = threading.Event()

        def blocking_fn(progress_callback=None):
            event.wait(timeout=5)
            return "done"

        tm = TaskManager()
        task_id = tm.start_task("scan", blocking_fn)
        time.sleep(0.1)

        assert tm.has_running_task() is True
        assert tm.has_running_task("scan") is True
        assert tm.has_running_task("analyze") is False

        event.set()
        time.sleep(0.3)
        assert tm.has_running_task("scan") is False

    def test_get_nonexistent_task(self):
        tm = TaskManager()
        assert tm.get_task("nonexistent") is None


# --- Flask App tests ---


def make_patent(**kwargs) -> Patent:
    defaults = {
        "patent_number": "D1012345",
        "title": "Eyeglasses Frame",
        "issue_date": date(2026, 2, 18),
        "classification_us": "D16/300",
    }
    defaults.update(kwargs)
    return Patent(**defaults)


@pytest.fixture
def app(tmp_path):
    """Create a test Flask app."""
    # Create a minimal config file
    config_content = f"""
api:
  base_url: "https://api.uspto.gov"
  rate_limit_per_minute: 50
  timeout_seconds: 30
  max_retries: 3

search_criteria:
  - name: "Test"
    us_classes:
      - "D16/300"
    keywords:
      - "eyeglasses"

initial_lookback_days: 30

notifications:
  email:
    enabled: false
    recipients:
      - "test@example.com"

sources:
  uspto_api: true
  official_gazette: false

database:
  path: "{tmp_path / 'test.db'}"

logging:
  level: "DEBUG"
  file: ""

ai:
  enabled: false

web:
  host: "127.0.0.1"
  port: 8080
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_content)

    # Ensure no real API keys are picked up
    os.environ.pop("USPTO_API_KEY", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)

    app = create_app(config_path=str(config_file))
    app.config["TESTING"] = True

    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


class TestDashboard:
    def test_dashboard_loads(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert b"Dashboard" in response.data
        assert b"Scan for Patents" in response.data

    def test_dashboard_shows_stats(self, app, client):
        # Insert a patent
        db = app.config["DB"]
        patent = make_patent()
        db.insert_patent(patent, ["D16/300"])

        response = client.get("/")
        assert response.status_code == 200
        assert b"D1012345" in response.data

    def test_dashboard_ai_disabled_message(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert b"AI Analyze (Disabled)" in response.data


class TestPatentList:
    def test_patent_list_loads(self, client):
        response = client.get("/patents")
        assert response.status_code == 200
        assert b"Patents" in response.data

    def test_patent_list_shows_patents(self, app, client):
        db = app.config["DB"]
        patent = make_patent()
        db.insert_patent(patent, ["D16/300"])

        response = client.get("/patents")
        assert response.status_code == 200
        assert b"D1012345" in response.data

    def test_patent_list_filter_by_status(self, app, client):
        db = app.config["DB"]
        patent = make_patent()
        db.insert_patent(patent, ["D16/300"])

        response = client.get("/patents?status=new")
        assert response.status_code == 200
        assert b"D1012345" in response.data

        response = client.get("/patents?status=flagged")
        assert response.status_code == 200


class TestPatentDetail:
    def test_patent_detail_loads(self, app, client):
        db = app.config["DB"]
        patent = make_patent()
        db.insert_patent(patent, ["D16/300"])

        response = client.get("/patents/D1012345")
        assert response.status_code == 200
        assert b"D1012345" in response.data
        assert b"Eyeglasses Frame" in response.data

    def test_patent_detail_not_found(self, client):
        response = client.get("/patents/NONEXISTENT")
        assert response.status_code == 404

    def test_patent_detail_with_analysis(self, app, client):
        db = app.config["DB"]
        patent = make_patent()
        db.insert_patent(patent, ["D16/300"])

        analysis = json.dumps({
            "similarity_score": 72,
            "risk_level": "high",
            "recommendation": "flag",
            "reasoning": "Very similar design",
            "patent_image_used": True,
            "product_images_used": ["product.png"],
            "model_used": "test-model",
            "analyzed_at": "2026-02-18T10:00:00",
            "error": None,
        })
        db.update_ai_analysis("D1012345", analysis)

        response = client.get("/patents/D1012345")
        assert response.status_code == 200
        assert b"72%" in response.data
        assert b"HIGH" in response.data


class TestApiEndpoints:
    @patch("patent_monitor.web.routes.run_scan")
    def test_api_scan(self, mock_run_scan, client):
        response = client.post("/api/scan")
        assert response.status_code == 200
        data = response.get_json()
        assert "task_id" in data

    def test_api_analyze_disabled(self, client):
        response = client.post("/api/analyze")
        data = response.get_json()
        assert response.status_code == 400
        assert "not enabled" in data["error"]

    def test_api_task_status_not_found(self, client):
        response = client.get("/api/tasks/nonexistent")
        assert response.status_code == 404

    def test_api_task_status(self, app, client):
        tm = app.config["TASK_MANAGER"]
        task_id = tm.start_task("test", lambda progress_callback=None: {"ok": True})
        time.sleep(0.3)

        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "completed"

    def test_api_update_status(self, app, client):
        db = app.config["DB"]
        patent = make_patent()
        db.insert_patent(patent, ["D16/300"])

        response = client.post(
            "/api/patents/D1012345/status",
            json={"status": "flagged"},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

        # Verify status changed
        updated = db.get_patent("D1012345")
        assert updated.status == "flagged"

    def test_api_update_status_invalid(self, app, client):
        db = app.config["DB"]
        patent = make_patent()
        db.insert_patent(patent, ["D16/300"])

        response = client.post(
            "/api/patents/D1012345/status",
            json={"status": "invalid"},
        )
        assert response.status_code == 400

    def test_api_update_status_not_found(self, client):
        response = client.post(
            "/api/patents/NONEXISTENT/status",
            json={"status": "flagged"},
        )
        assert response.status_code == 404

    def test_api_analyze_single_not_found(self, client):
        # Need AI enabled for this test
        pass  # Would need AI-enabled config

    def test_api_scan_duplicate_rejected(self, app, client):
        """Test that starting a scan while one is running returns 409."""
        import threading

        event = threading.Event()
        tm = app.config["TASK_MANAGER"]

        # Start a blocking "scan" task
        tm.start_task("scan", lambda progress_callback=None: event.wait(timeout=5))
        time.sleep(0.1)

        response = client.post("/api/scan")
        assert response.status_code == 409
        data = response.get_json()
        assert "already running" in data["error"]

        event.set()
