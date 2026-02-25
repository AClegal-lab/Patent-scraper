"""Tests for the service layer."""

import json
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from patent_monitor.config import (
    AiConfig,
    ApiConfig,
    Config,
    LoggingConfig,
    NotificationConfig,
    SearchCriteriaConfig,
    SourcesConfig,
)
from patent_monitor.db import Database
from patent_monitor.models import Alert, AnalysisResult, Patent
from patent_monitor.service import (
    AiAnalysisResult,
    ScanResult,
    get_dashboard_stats,
    run_ai_analysis,
    run_scan,
)


def make_config(**overrides) -> Config:
    """Create a test config."""
    config = Config(
        api=ApiConfig(api_key="test-key"),
        search_criteria=[
            SearchCriteriaConfig(
                name="Test",
                us_classes=["D16/300"],
                keywords=["eyeglasses"],
            )
        ],
        sources=SourcesConfig(uspto_api=True, official_gazette=False),
        database_path=":memory:",
        logging=LoggingConfig(level="DEBUG", file=""),
        ai=AiConfig(enabled=False),
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def make_patent(**kwargs) -> Patent:
    defaults = {
        "patent_number": "D1012345",
        "title": "Eyeglasses Frame",
        "issue_date": date(2026, 2, 18),
        "classification_us": "D16/300",
    }
    defaults.update(kwargs)
    return Patent(**defaults)


@patch("patent_monitor.service.USPTOClient")
def test_run_scan_basic(mock_client_cls):
    """Test basic scan returns results."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    patent = make_patent()
    mock_client.search_design_patents.return_value = [patent]

    config = make_config()
    db = Database(":memory:")
    with db:
        result = run_scan(config, db)

        assert isinstance(result, ScanResult)
        assert result.total_fetched == 1
        assert result.new_matches == 1
        assert len(result.alerts) == 1
        assert result.alerts[0].patent.patent_number == "D1012345"


@patch("patent_monitor.service.USPTOClient")
def test_run_scan_no_matches(mock_client_cls):
    """Test scan with no matching patents."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    # Patent with non-matching classification
    patent = make_patent(
        patent_number="D9999999",
        title="Unrelated Widget",
        classification_us="D99/999",
    )
    mock_client.search_design_patents.return_value = [patent]

    config = make_config()
    db = Database(":memory:")
    with db:
        result = run_scan(config, db)
        assert result.new_matches == 0
        assert len(result.alerts) == 0


@patch("patent_monitor.service.USPTOClient")
def test_run_scan_deduplicates(mock_client_cls):
    """Test scan skips patents already in DB."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    patent = make_patent()
    mock_client.search_design_patents.return_value = [patent]

    config = make_config()
    db = Database(":memory:")
    with db:
        # Insert the patent first
        db.insert_patent(patent, ["D16/300"])

        result = run_scan(config, db)
        assert result.new_matches == 0


@patch("patent_monitor.service.USPTOClient")
def test_run_scan_with_progress_callback(mock_client_cls):
    """Test that progress callback is called."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.search_design_patents.return_value = []

    config = make_config()
    db = Database(":memory:")
    messages = []

    with db:
        run_scan(config, db, progress_callback=messages.append)

    assert len(messages) >= 2  # at least "Searching..." and "Scan complete"
    assert any("Searching" in m for m in messages)


@patch("patent_monitor.service.USPTOClient")
def test_run_scan_handles_api_error(mock_client_cls):
    """Test scan handles API errors gracefully."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.search_design_patents.side_effect = RuntimeError("API down")

    config = make_config()
    db = Database(":memory:")
    with db:
        result = run_scan(config, db)
        assert len(result.errors) == 1
        assert "API" in result.errors[0]


def test_get_dashboard_stats():
    """Test dashboard stats computation."""
    db = Database(":memory:")
    with db:
        # Insert a patent
        patent = make_patent()
        db.insert_patent(patent, ["D16/300"])

        stats = get_dashboard_stats(db)
        assert stats["total_patents"] == 1
        assert stats["pending_analysis"] == 1
        assert "new" in stats["counts_by_status"]


def test_get_dashboard_stats_empty_db():
    """Test dashboard stats with empty database."""
    db = Database(":memory:")
    with db:
        stats = get_dashboard_stats(db)
        assert stats["total_patents"] == 0
        assert stats["pending_analysis"] == 0
        assert stats["high_risk_count"] == 0


@patch("patent_monitor.service.PatentAnalyzer")
@patch("patent_monitor.service.PatentImageFetcher")
@patch("patent_monitor.service.load_product_images")
def test_run_ai_analysis_disabled(mock_load_imgs, mock_fetcher_cls, mock_analyzer_cls):
    """Test AI analysis returns error when disabled."""
    config = make_config(ai=AiConfig(enabled=False))
    db = Database(":memory:")
    with db:
        result = run_ai_analysis(config, db)
        assert len(result.errors) == 1
        assert "not enabled" in result.errors[0]


@patch("patent_monitor.service.PatentAnalyzer")
@patch("patent_monitor.service.PatentImageFetcher")
@patch("patent_monitor.service.load_product_images")
def test_run_ai_analysis_no_product_images(mock_load_imgs, mock_fetcher_cls, mock_analyzer_cls):
    """Test AI analysis returns error when no product images."""
    mock_load_imgs.return_value = []
    config = make_config(ai=AiConfig(enabled=True, api_key="test-key"))
    db = Database(":memory:")
    with db:
        result = run_ai_analysis(config, db)
        assert len(result.errors) == 1
        assert "product images" in result.errors[0].lower()


@patch("patent_monitor.service.PatentAnalyzer")
@patch("patent_monitor.service.PatentImageFetcher")
@patch("patent_monitor.service.load_product_images")
def test_run_ai_analysis_analyzes_patents(mock_load_imgs, mock_fetcher_cls, mock_analyzer_cls):
    """Test AI analysis runs on unanalyzed patents."""
    mock_load_imgs.return_value = [("product.png", b"image_data")]
    mock_fetcher = MagicMock()
    mock_fetcher_cls.return_value = mock_fetcher
    mock_fetcher.fetch_patent_image.return_value = b"patent_image"

    mock_analyzer = MagicMock()
    mock_analyzer_cls.return_value = mock_analyzer
    mock_analyzer.analyze.return_value = AnalysisResult(
        similarity_score=72,
        risk_level="high",
        recommendation="flag",
        reasoning="Very similar design",
        patent_image_used=True,
        product_images_used=["product.png"],
        model_used="test-model",
        analyzed_at=datetime.now(),
    )

    config = make_config(ai=AiConfig(enabled=True, api_key="test-key"))
    db = Database(":memory:")
    with db:
        patent = make_patent()
        db.insert_patent(patent, ["D16/300"])

        result = run_ai_analysis(config, db)
        assert len(result.analyzed) == 1
        assert result.analyzed[0][0] == "D1012345"
        assert result.analyzed[0][1].similarity_score == 72

        # Check it was stored in DB
        analysis_json = db.get_ai_analysis("D1012345")
        assert analysis_json is not None
        data = json.loads(analysis_json)
        assert data["similarity_score"] == 72
