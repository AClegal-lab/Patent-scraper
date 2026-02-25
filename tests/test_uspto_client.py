"""Tests for USPTO ODP API client."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from patent_monitor.api.uspto_client import USPTOClient


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


@patch("patent_monitor.api.uspto_client.requests.Session")
def test_search_design_patents(mock_session_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    fixture = load_fixture("sample_api_response.json")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = fixture
    mock_response.raise_for_status.return_value = None
    mock_session.request.return_value = mock_response

    client = USPTOClient(api_key="test-key", rate_limit=1000)
    client.session = mock_session

    patents = client.search_design_patents(
        date_from=date(2026, 2, 1),
        date_to=date(2026, 2, 24),
    )

    assert len(patents) == 3
    assert patents[0].patent_number == "D1012345"
    assert patents[0].title == "Eyeglasses Frame"
    assert patents[0].assignee == "Acme Eyewear Inc."
    assert patents[0].classification_us == "D16/300"
    assert patents[0].issue_date == date(2026, 2, 18)


@patch("patent_monitor.api.uspto_client.requests.Session")
def test_search_with_keywords(mock_session_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    fixture = load_fixture("sample_api_response.json")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = fixture
    mock_response.raise_for_status.return_value = None
    mock_session.request.return_value = mock_response

    client = USPTOClient(api_key="test-key", rate_limit=1000)
    client.session = mock_session

    patents = client.search_design_patents(
        date_from=date(2026, 2, 1),
        date_to=date(2026, 2, 24),
        keywords=["eyeglasses"],
    )

    # Check the request used GET with query params containing the keyword
    call_args = mock_session.request.call_args
    params = call_args.kwargs.get("params") or call_args[1].get("params")
    assert "eyeglasses" in params["q"]


@patch("patent_monitor.api.uspto_client.requests.Session")
def test_parse_patent_fields(mock_session_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    fixture = load_fixture("sample_api_response.json")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = fixture
    mock_response.raise_for_status.return_value = None
    mock_session.request.return_value = mock_response

    client = USPTOClient(api_key="test-key", rate_limit=1000)
    client.session = mock_session

    patents = client.search_design_patents(date(2026, 2, 1), date(2026, 2, 24))

    # Check sunglasses patent
    sunglasses = next(p for p in patents if "Sunglasses" in p.title)
    assert sunglasses.patent_number == "D1012346"
    assert sunglasses.assignee == "SunVision Corp."
    assert sunglasses.classification_cpc == "G02C 5/00"
    assert sunglasses.inventors == ["Lee, Chris"]

    # Check table lamp (non-eyewear)
    lamp = next(p for p in patents if "Lamp" in p.title)
    assert lamp.classification_us == "D26/100"


def test_build_query():
    client = USPTOClient(api_key="test-key")
    q = client._build_query(
        date_from=date(2026, 2, 1),
        date_to=date(2026, 2, 24),
        keywords=["eyeglasses", "sunglasses"],
    )

    assert "applicationMetaData.applicationTypeLabelName:Design" in q
    assert "applicationMetaData.applicationStatusCode:150" in q
    assert "applicationMetaData.grantDate:[2026-02-01 TO 2026-02-24]" in q
    assert '"eyeglasses"' in q
    assert '"sunglasses"' in q


def test_build_query_no_keywords():
    client = USPTOClient(api_key="test-key")
    q = client._build_query(
        date_from=date(2026, 2, 1),
        date_to=date(2026, 2, 24),
        keywords=None,
    )
    assert "applicationMetaData.applicationTypeLabelName:Design" in q
    assert "applicationMetaData.applicationStatusCode:150" in q
    assert "eyeglasses" not in q


@patch("patent_monitor.api.uspto_client.requests.Session")
def test_404_returns_empty(mock_session_cls):
    """API returns 404 when no results found — should return empty list."""
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_session.request.return_value = mock_response

    client = USPTOClient(api_key="test-key", rate_limit=1000)
    client.session = mock_session

    patents = client.search_design_patents(date(2026, 2, 1), date(2026, 2, 24))
    assert patents == []
