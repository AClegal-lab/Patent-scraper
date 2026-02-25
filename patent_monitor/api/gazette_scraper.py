"""Official Gazette scraper for supplementary design patent data."""

import logging
import re
import time
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from ..models import Patent

logger = logging.getLogger(__name__)


class GazetteScraper:
    """Scrapes the USPTO Official Gazette for design patent listings."""

    BASE_URL = "https://www.uspto.gov/web/patents/patog/week{week}/OG/html/Designs.html"
    # Alternative URL patterns for the gazette
    ALT_URLS = [
        "https://patentsgazette.uspto.gov/week{week}/OG/Designs.html",
        "https://www.uspto.gov/web/patents/patog/week{week}/OG/html/Designs.html",
    ]

    def __init__(self, delay_seconds: float = 2.0, timeout: int = 30):
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PatentMonitor/1.0 (Design Patent Research Tool)",
        })

    def scrape_current_week(self) -> list[Patent]:
        """Scrape the current week's Official Gazette for design patents."""
        week_num = self._get_current_week()
        return self.scrape_week(week_num)

    def scrape_week(self, week_num: int) -> list[Patent]:
        """Scrape a specific week's Official Gazette.

        Args:
            week_num: Week number (1-52).

        Returns:
            List of Patent objects found in the gazette.
        """
        week_str = str(week_num).zfill(2)
        patents = []

        for url_template in self.ALT_URLS:
            url = url_template.format(week=week_str)
            logger.info(f"Fetching Official Gazette: {url}")

            try:
                time.sleep(self.delay_seconds)
                response = self.session.get(url, timeout=self.timeout)

                if response.status_code == 404:
                    logger.debug(f"Gazette page not found at {url}, trying next URL")
                    continue

                response.raise_for_status()
                patents = self._parse_gazette_page(response.text)
                logger.info(f"Found {len(patents)} design patents in gazette week {week_num}")
                return patents

            except requests.exceptions.RequestException as e:
                logger.warning(f"Failed to fetch gazette from {url}: {e}")
                continue

        logger.warning(f"Could not fetch gazette for week {week_num} from any URL")
        return patents

    def _parse_gazette_page(self, html: str) -> list[Patent]:
        """Parse the Official Gazette HTML page for design patent listings."""
        soup = BeautifulSoup(html, "lxml")
        patents = []

        # The gazette typically lists patents in table rows or structured divs.
        # The exact structure varies, so we try multiple selectors.

        # Try parsing table-based layout
        for row in soup.select("table tr"):
            patent = self._parse_gazette_row(row)
            if patent:
                patents.append(patent)

        # Try parsing div-based layout if no table results
        if not patents:
            for entry in soup.select("div.patent-entry, div.design-entry, .patentEntry"):
                patent = self._parse_gazette_div(entry)
                if patent:
                    patents.append(patent)

        # Fallback: look for patent number patterns in the page text
        if not patents:
            patents = self._parse_gazette_text(soup)

        return patents

    def _parse_gazette_row(self, row) -> Patent | None:
        """Parse a table row from the gazette."""
        cells = row.find_all("td")
        if len(cells) < 2:
            return None

        text = row.get_text(" ", strip=True)

        # Look for design patent number pattern: D followed by digits
        patent_match = re.search(r"D[\s,]*(\d[\d,]+\d)", text)
        if not patent_match:
            return None

        patent_number = "D" + patent_match.group(1).replace(",", "")
        title = ""
        classification = ""

        # Try to extract title and classification from cells
        for cell in cells:
            cell_text = cell.get_text(strip=True)
            # Classification pattern: D16/300 or similar
            class_match = re.search(r"D\d+/\d+", cell_text)
            if class_match:
                classification = class_match.group(0)
            elif len(cell_text) > 5 and not cell_text.startswith("D"):
                title = cell_text

        if not title:
            title = text[:100]

        # Extract image URL if present
        img = row.find("img")
        image_url = img.get("src") if img else None

        return Patent(
            patent_number=patent_number,
            title=title,
            issue_date=date.today(),
            classification_us=classification,
            image_url=image_url,
        )

    def _parse_gazette_div(self, div) -> Patent | None:
        """Parse a div entry from the gazette."""
        text = div.get_text(" ", strip=True)

        patent_match = re.search(r"D[\s,]*(\d[\d,]+\d)", text)
        if not patent_match:
            return None

        patent_number = "D" + patent_match.group(1).replace(",", "")

        class_match = re.search(r"D\d+/\d+", text)
        classification = class_match.group(0) if class_match else ""

        img = div.find("img")
        image_url = img.get("src") if img else None

        # Remove patent number and classification from text to get title
        title = text
        if patent_match:
            title = title.replace(patent_match.group(0), "").strip()
        title = title[:200] if title else ""

        return Patent(
            patent_number=patent_number,
            title=title,
            issue_date=date.today(),
            classification_us=classification,
            image_url=image_url,
        )

    def _parse_gazette_text(self, soup: BeautifulSoup) -> list[Patent]:
        """Fallback: extract patent numbers from raw page text."""
        text = soup.get_text()
        patents = []

        # Find all design patent numbers
        for match in re.finditer(r"D[\s,]*(\d[\d,]+\d)", text):
            patent_number = "D" + match.group(1).replace(",", "")
            # Avoid duplicates
            if not any(p.patent_number == patent_number for p in patents):
                patents.append(Patent(
                    patent_number=patent_number,
                    title="(from Official Gazette)",
                    issue_date=date.today(),
                ))

        return patents

    def _get_current_week(self) -> int:
        """Get the current ISO week number."""
        return date.today().isocalendar()[1]
