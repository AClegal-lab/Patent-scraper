"""Patent image fetching and product image loading."""

import logging
import re
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class PatentImageFetcher:
    """Fetches patent design drawing images from USPTO and related sources."""

    def __init__(self, timeout: int = 30, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PatentMonitor/1.0 (Design Patent Research Tool)",
        })

    def fetch_patent_image(self, patent) -> bytes | None:
        """Fetch the primary design drawing for a patent.

        Tries multiple URL strategies in order:
        1. patent.image_url if populated
        2. USPTO PPUBS patent PDF (converted to PNG)
        3. Google Patents image mirror
        4. USPTO full-image endpoint (legacy)

        Returns PNG image bytes, or None if all methods fail.
        """
        strategies = [
            ("direct_url", lambda: self._fetch_direct(patent)),
            ("ppubs_pdf", lambda: self._fetch_ppubs_pdf(patent)),
            ("google_patents", lambda: self._fetch_google_patents(patent)),
            ("uspto_image", lambda: self._fetch_uspto_image(patent)),
        ]

        for name, fetch_fn in strategies:
            try:
                result = fetch_fn()
                if result:
                    logger.info(f"Fetched patent image for {patent.patent_number} via {name}")
                    return result
            except Exception as e:
                logger.debug(f"Image fetch strategy '{name}' failed for {patent.patent_number}: {e}")
                continue

        logger.warning(f"Could not fetch image for patent {patent.patent_number}")
        return None

    def _fetch_direct(self, patent) -> bytes | None:
        """Fetch from the patent's image_url field (set by gazette scraper)."""
        if not patent.image_url:
            return None
        return self._download(patent.image_url)

    def _fetch_ppubs_pdf(self, patent) -> bytes | None:
        """Fetch patent PDF from USPTO PPUBS and convert first drawing to PNG."""
        num = patent.patent_number.replace(",", "")
        if not num.startswith("D"):
            num = f"D{num}"

        url = f"https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/{num}"
        pdf_bytes = self._download(url)
        if pdf_bytes:
            return self._convert_pdf_to_png(pdf_bytes, auto_detect=True)
        return None

    def _fetch_google_patents(self, patent) -> bytes | None:
        """Fetch from Google Patents image mirror."""
        # Google Patents stores images at predictable URLs
        num = patent.patent_number.replace(",", "")
        if not num.startswith("D"):
            num = f"D{num}"

        # Try common Google Patents image URL patterns
        urls = [
            f"https://patentimages.storage.googleapis.com/US{num}-20{patent.issue_date.strftime('%y%m%d')}-D00001.png",
            f"https://patentimages.storage.googleapis.com/US{num}-D00001.png",
        ]

        for url in urls:
            result = self._download(url)
            if result:
                return result
        return None

    def _fetch_uspto_image(self, patent) -> bytes | None:
        """Fetch from USPTO patent full-image endpoint."""
        num = patent.patent_number.replace(",", "").replace("D", "")

        # USPTO stores patent PDFs at segmented paths
        # Example: D1012345 -> num=1012345 -> segments: 45/123/D10/0.pdf
        if len(num) >= 6:
            suffix = num[-2:]        # last 2 digits
            mid = num[-5:-2]         # middle 3 digits
            prefix = f"D{num[:-5]}"  # remainder with D prefix

            url = f"https://pimg-fpiw.uspto.gov/fdd/{suffix}/{mid}/{prefix}/0.pdf"
            pdf_bytes = self._download(url)
            if pdf_bytes:
                return self._convert_pdf_to_png(pdf_bytes)

        return None

    def _download(self, url: str) -> bytes | None:
        """Download from a URL with retries."""
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout)
                if response.status_code == 200 and len(response.content) > 100:
                    return response.content
                elif response.status_code == 404:
                    return None
                else:
                    logger.debug(f"Download {url}: status={response.status_code}, size={len(response.content)}")
            except requests.exceptions.RequestException as e:
                logger.debug(f"Download attempt {attempt} failed for {url}: {e}")
                if attempt < self.max_retries:
                    time.sleep(1)
        return None

    def _find_best_drawing_page(self, doc) -> int:
        """Find the page most likely to contain a design drawing.

        Design patent PDFs typically have:
        - Page 0: Cover/title page (lots of text, patent metadata)
        - Page 1+: Design drawings (minimal text, mostly graphics)

        Uses two strategies:
        1. Text-based scoring when pages have extractable text.
        2. Pixel-based white-space analysis for scanned PDFs where all
           pages are images with no extractable text. Drawing pages have
           more white space than text/description pages.
        """
        if len(doc) <= 1:
            return 0

        # Check if this is a scanned PDF (no extractable text on any page)
        has_text = any(doc[i].get_text().strip() for i in range(min(len(doc), 4)))

        if has_text:
            return self._score_by_text(doc)
        else:
            return self._score_by_whitespace(doc)

    def _score_by_text(self, doc) -> int:
        """Score pages by text content — less text means more likely a drawing."""
        best_idx = 1
        best_score = -1

        for idx in range(len(doc)):
            if idx == 0:
                continue

            page = doc[idx]
            text_len = len(page.get_text().strip())
            image_list = page.get_images(full=True)

            score = 0
            if image_list:
                score += 100
            if text_len < 50:
                score += 50
            elif text_len < 200:
                score += 20
            elif text_len > 500:
                score -= 30

            if score > best_score:
                best_score = score
                best_idx = idx

        logger.debug(f"Text-based best page: {best_idx} (score={best_score})")
        return best_idx

    def _score_by_whitespace(self, doc) -> int:
        """Score pages by white-space ratio for scanned/image-only PDFs.

        Cover/description pages are text-heavy (~92% white).
        Drawing pages are sparser (~96-99% white).
        Among drawings, the first figure (perspective view) has the most
        detail and makes the best thumbnail — lowest white ratio above 95%.
        """
        best_idx = 1
        best_white = 1.0

        # Check first several pages (drawings are near the front)
        for idx in range(1, min(len(doc), 6)):
            page = doc[idx]
            pix = page.get_pixmap(dpi=36)  # low-res for speed
            samples = pix.samples
            total = pix.width * pix.height
            white = 0
            for i in range(0, len(samples), pix.n):
                if samples[i] > 240 and samples[i + 1] > 240 and samples[i + 2] > 240:
                    white += 1
            ratio = white / total

            # Skip text-heavy pages (<97% white = cover, references, description)
            if ratio < 0.97:
                continue
            # Skip nearly blank pages (>99.5% white)
            if ratio > 0.995:
                continue

            # Prefer the page with the MOST drawing content (lowest white ratio)
            if ratio < best_white:
                best_white = ratio
                best_idx = idx

        logger.debug(f"Whitespace-based best page: {best_idx} (white={best_white:.1%})")
        return best_idx

    def _convert_pdf_to_png(self, pdf_bytes: bytes, page_index: int = 0, auto_detect: bool = False) -> bytes | None:
        """Convert a page of a PDF to a PNG image.

        Args:
            pdf_bytes: Raw PDF data.
            page_index: Which page to convert (0-based). Falls back to page 0
                        if the requested page doesn't exist.
            auto_detect: If True, ignore page_index and automatically find the
                         best drawing page.
        """
        try:
            import fitz  # pymupdf

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if len(doc) == 0:
                return None

            if auto_detect:
                idx = self._find_best_drawing_page(doc)
            else:
                idx = page_index if page_index < len(doc) else 0

            page = doc[idx]
            pix = page.get_pixmap(dpi=150)
            png_bytes = pix.tobytes("png")
            doc.close()
            return png_bytes
        except ImportError:
            logger.warning("pymupdf not installed -- cannot convert patent PDF to image. Install with: pip install pymupdf")
            return None
        except Exception as e:
            logger.error(f"PDF to PNG conversion failed: {e}")
            return None


def load_product_images(directory: str, max_images: int = 3) -> list[tuple[str, bytes]]:
    """Load product reference images from a directory.

    Args:
        directory: Path to directory containing product images.
        max_images: Maximum number of images to load (controls API cost).

    Returns:
        List of (filename, image_bytes) tuples, sorted by filename.
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        logger.warning(f"Product images directory not found: {directory}")
        return []

    images = []
    for file_path in sorted(dir_path.iterdir()):
        if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            try:
                image_bytes = file_path.read_bytes()
                images.append((file_path.name, image_bytes))
                logger.debug(f"Loaded product image: {file_path.name} ({len(image_bytes)} bytes)")
            except IOError as e:
                logger.warning(f"Failed to read image {file_path}: {e}")

    if len(images) > max_images:
        logger.info(f"Limiting product images from {len(images)} to {max_images}")
        images = images[:max_images]

    return images
