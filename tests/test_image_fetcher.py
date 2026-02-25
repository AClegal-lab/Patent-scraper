"""Tests for patent image fetching and product image loading."""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from patent_monitor.image_fetcher import PatentImageFetcher, load_product_images
from patent_monitor.models import Patent


# PNG header + padding to exceed 100 byte minimum in _download()
TINY_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
    b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05'
    b'\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
    + b'\x00' * 50  # padding to get over 100 bytes
)


def make_patent(**kwargs) -> Patent:
    defaults = {
        "patent_number": "D1012345",
        "title": "Eyeglasses Frame",
        "issue_date": date(2026, 2, 18),
    }
    defaults.update(kwargs)
    return Patent(**defaults)


@patch("patent_monitor.image_fetcher.requests.Session")
def test_fetch_from_direct_url(mock_session_cls):
    """Test fetching from patent.image_url."""
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = TINY_PNG
    mock_session.get.return_value = mock_response

    fetcher = PatentImageFetcher()
    fetcher.session = mock_session

    patent = make_patent(image_url="https://example.com/patent.png")
    result = fetcher.fetch_patent_image(patent)

    assert result == TINY_PNG


@patch("patent_monitor.image_fetcher.requests.Session")
def test_fetch_returns_none_when_all_fail(mock_session_cls):
    """Test graceful failure when no strategy works."""
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_session.get.return_value = mock_response

    fetcher = PatentImageFetcher()
    fetcher.session = mock_session

    patent = make_patent(image_url=None)
    result = fetcher.fetch_patent_image(patent)

    assert result is None


@patch("patent_monitor.image_fetcher.requests.Session")
def test_fetch_skips_small_responses(mock_session_cls):
    """Test that tiny responses (<100 bytes) are treated as failures."""
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"error"  # too small
    mock_session.get.return_value = mock_response

    fetcher = PatentImageFetcher()
    fetcher.session = mock_session

    patent = make_patent(image_url="https://example.com/broken.png")
    result = fetcher.fetch_patent_image(patent)
    assert result is None


def test_load_product_images():
    """Test loading product images from a directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test image files
        (Path(tmpdir) / "product_a.png").write_bytes(TINY_PNG)
        (Path(tmpdir) / "product_b.jpg").write_bytes(b'\xff\xd8' + b'\x00' * 200)
        (Path(tmpdir) / "readme.txt").write_text("not an image")

        images = load_product_images(tmpdir, max_images=10)

        assert len(images) == 2
        filenames = [name for name, _ in images]
        assert "product_a.png" in filenames
        assert "product_b.jpg" in filenames
        assert "readme.txt" not in filenames


def test_load_product_images_max_limit():
    """Test that max_images limit is enforced."""
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(5):
            (Path(tmpdir) / f"product_{i:02d}.png").write_bytes(TINY_PNG)

        images = load_product_images(tmpdir, max_images=2)
        assert len(images) == 2


def test_load_product_images_empty_dir():
    """Test loading from a directory with no images."""
    with tempfile.TemporaryDirectory() as tmpdir:
        images = load_product_images(tmpdir)
        assert images == []


def test_load_product_images_missing_dir():
    """Test loading from a nonexistent directory."""
    images = load_product_images("/nonexistent/path")
    assert images == []


def test_load_product_images_sorted():
    """Test that images are returned sorted by filename."""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "c_product.png").write_bytes(TINY_PNG)
        (Path(tmpdir) / "a_product.png").write_bytes(TINY_PNG)
        (Path(tmpdir) / "b_product.png").write_bytes(TINY_PNG)

        images = load_product_images(tmpdir, max_images=10)
        filenames = [name for name, _ in images]
        assert filenames == ["a_product.png", "b_product.png", "c_product.png"]


def test_convert_pdf_to_png():
    """Test PDF conversion (if pymupdf is available)."""
    fetcher = PatentImageFetcher()

    # Create a minimal valid PDF
    try:
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=100, height=100)
        pdf_bytes = doc.tobytes()
        doc.close()

        result = fetcher._convert_pdf_to_png(pdf_bytes)
        assert result is not None
        assert result[:8] == b'\x89PNG\r\n\x1a\n'  # PNG magic bytes
    except ImportError:
        pytest.skip("pymupdf not installed")
