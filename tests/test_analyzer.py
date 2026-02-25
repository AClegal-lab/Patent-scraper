"""Tests for AI patent analyzer."""

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from patent_monitor.analyzer import PatentAnalyzer
from patent_monitor.config import AiConfig
from patent_monitor.models import Patent


FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Minimal 1x1 white PNG for testing
TINY_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
    b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05'
    b'\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
)


def make_config(**kwargs) -> AiConfig:
    defaults = {
        "enabled": True,
        "api_key": "test-key",
        "model": "claude-sonnet-4-20250514",
        "rate_limit_per_minute": 1000,  # no rate limiting in tests
        "max_tokens": 1024,
        "timeout_seconds": 30,
        "product_images_dir": "/tmp/test",
        "similarity_threshold": 30,
        "max_product_images": 3,
    }
    defaults.update(kwargs)
    return AiConfig(**defaults)


def make_patent(**kwargs) -> Patent:
    defaults = {
        "patent_number": "D1012345",
        "title": "Eyeglasses Frame",
        "issue_date": date(2026, 2, 18),
        "assignee": "Acme Eyewear Inc.",
        "classification_us": "D16/300",
        "classification_cpc": "G02C 1/00",
        "abstract": "An ornamental design for eyeglasses frame.",
    }
    defaults.update(kwargs)
    return Patent(**defaults)


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


@patch("patent_monitor.analyzer.anthropic.Anthropic")
def test_analyze_with_images(mock_anthropic_cls):
    """Test full analysis with patent image and product images."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    fixture = load_fixture("sample_analysis_response.json")
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(fixture))]
    mock_client.messages.create.return_value = mock_response

    config = make_config()
    analyzer = PatentAnalyzer(config)
    analyzer.client = mock_client

    result = analyzer.analyze(
        patent=make_patent(),
        patent_image=TINY_PNG,
        product_images=[("product1.png", TINY_PNG)],
    )

    assert result.similarity_score == 72
    assert result.risk_level == "high"
    assert result.recommendation == "flag"
    assert "wraparound" in result.reasoning
    assert result.patent_image_used is True
    assert result.product_images_used == ["product1.png"]
    assert result.error is None


@patch("patent_monitor.analyzer.anthropic.Anthropic")
def test_analyze_text_only_fallback(mock_anthropic_cls):
    """Test analysis without patent image (text-only)."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    response_data = {
        "similarity_score": 35,
        "risk_level": "low",
        "recommendation": "monitor",
        "reasoning": "Text-only analysis suggests moderate overlap in design category.",
    }
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(response_data))]
    mock_client.messages.create.return_value = mock_response

    config = make_config()
    analyzer = PatentAnalyzer(config)
    analyzer.client = mock_client

    result = analyzer.analyze(
        patent=make_patent(),
        patent_image=None,  # No patent image
        product_images=[("product1.png", TINY_PNG)],
    )

    assert result.similarity_score == 35
    assert result.patent_image_used is False

    # Check that system prompt included text-only note
    call_args = mock_client.messages.create.call_args
    system = call_args.kwargs.get("system", "")
    assert "no patent drawing image" in system.lower() or "text-only" in system.lower()


@patch("patent_monitor.analyzer.anthropic.Anthropic")
def test_parse_response_json_in_markdown(mock_anthropic_cls):
    """Test parsing JSON wrapped in markdown code fences."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    # Claude sometimes wraps JSON in code fences
    raw_text = '```json\n{"similarity_score": 50, "risk_level": "medium", "recommendation": "monitor", "reasoning": "Moderate similarity."}\n```'
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=raw_text)]
    mock_client.messages.create.return_value = mock_response

    config = make_config()
    analyzer = PatentAnalyzer(config)
    analyzer.client = mock_client

    result = analyzer.analyze(make_patent(), TINY_PNG, [("p.png", TINY_PNG)])
    assert result.similarity_score == 50
    assert result.risk_level == "medium"


@patch("patent_monitor.analyzer.anthropic.Anthropic")
def test_parse_response_invalid(mock_anthropic_cls):
    """Test handling of unparseable response."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="I cannot analyze this image.")]
    mock_client.messages.create.return_value = mock_response

    config = make_config()
    analyzer = PatentAnalyzer(config)
    analyzer.client = mock_client

    result = analyzer.analyze(make_patent(), TINY_PNG, [("p.png", TINY_PNG)])
    assert result.similarity_score == 0
    assert result.error == "parse_failure"


@patch("patent_monitor.analyzer.anthropic.Anthropic")
def test_api_exception_returns_error_result(mock_anthropic_cls):
    """Test that API exceptions produce a safe error result."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.side_effect = Exception("Network error")

    config = make_config()
    analyzer = PatentAnalyzer(config)
    analyzer.client = mock_client

    result = analyzer.analyze(make_patent(), TINY_PNG, [("p.png", TINY_PNG)])
    assert result.similarity_score == 0
    assert result.recommendation == "monitor"
    assert "Network error" in result.error


def test_parse_response_valid_json():
    """Test _parse_response with clean JSON."""
    config = make_config()
    analyzer = PatentAnalyzer(config)

    result = analyzer._parse_response(json.dumps({
        "similarity_score": 85,
        "risk_level": "high",
        "recommendation": "flag",
        "reasoning": "Very similar designs.",
    }))
    assert result.similarity_score == 85
    assert result.risk_level == "high"
    assert result.recommendation == "flag"


def test_parse_response_clamps_score():
    """Test that scores are clamped to 0-100."""
    config = make_config()
    analyzer = PatentAnalyzer(config)

    result = analyzer._parse_response(json.dumps({
        "similarity_score": 150,
        "risk_level": "high",
        "recommendation": "flag",
        "reasoning": "Test.",
    }))
    assert result.similarity_score == 100

    result2 = analyzer._parse_response(json.dumps({
        "similarity_score": -10,
        "risk_level": "none",
        "recommendation": "dismiss",
        "reasoning": "Test.",
    }))
    assert result2.similarity_score == 0


def test_parse_response_invalid_enum_defaults():
    """Test that invalid enum values get safe defaults."""
    config = make_config()
    analyzer = PatentAnalyzer(config)

    result = analyzer._parse_response(json.dumps({
        "similarity_score": 50,
        "risk_level": "extreme",
        "recommendation": "sue_them",
        "reasoning": "Test.",
    }))
    assert result.risk_level == "none"
    assert result.recommendation == "monitor"


def test_guess_media_type():
    """Test image format detection."""
    config = make_config()
    analyzer = PatentAnalyzer(config)

    assert analyzer._guess_media_type(TINY_PNG) == "image/png"
    assert analyzer._guess_media_type(b'\xff\xd8\xff\xe0') == "image/jpeg"
    assert analyzer._guess_media_type(b'RIFF\x00\x00\x00\x00WEBP') == "image/webp"
    assert analyzer._guess_media_type(b'\x00\x00', "test.jpg") == "image/jpeg"


def test_build_messages_with_patent_image():
    """Test message structure includes patent image."""
    config = make_config()
    analyzer = PatentAnalyzer(config)

    messages = analyzer._build_messages(
        patent=make_patent(),
        patent_image=TINY_PNG,
        product_images=[("product.png", TINY_PNG)],
    )

    assert len(messages) == 1
    content = messages[0]["content"]
    # Should have: patent label, patent image, product label, product image, metadata text
    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 2  # patent image + product image

    text_blocks = [b for b in content if b.get("type") == "text"]
    assert any("PATENT DRAWING" in b["text"] for b in text_blocks)
    assert any("PRODUCT REFERENCE" in b["text"] for b in text_blocks)
    assert any("PATENT METADATA" in b["text"] for b in text_blocks)


def test_build_messages_without_patent_image():
    """Test message structure without patent image."""
    config = make_config()
    analyzer = PatentAnalyzer(config)

    messages = analyzer._build_messages(
        patent=make_patent(),
        patent_image=None,
        product_images=[("product.png", TINY_PNG)],
    )

    content = messages[0]["content"]
    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 1  # only product image
