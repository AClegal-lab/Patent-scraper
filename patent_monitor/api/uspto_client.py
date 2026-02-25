"""USPTO Open Data Portal (ODP) API client for patent search."""

import json
import logging
import time
from datetime import date, datetime

import requests

from ..models import Patent

logger = logging.getLogger(__name__)


class USPTOClient:
    """Client for the USPTO ODP Patent File Wrapper API.

    Uses the api.uspto.gov GET search endpoint with Lucene-style query syntax
    to find granted design patents.
    """

    BASE_URL = "https://api.uspto.gov"
    SEARCH_ENDPOINT = "/api/v1/patent/applications/search"

    def __init__(self, api_key: str, rate_limit: int = 50, timeout: int = 30, max_retries: int = 3):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = 60.0 / rate_limit  # seconds between requests
        self._last_request_time = 0.0
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key": api_key,
            "Accept": "application/json",
        })

    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_time = time.time()

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make an API request with retry logic."""
        url = f"{self.BASE_URL}{endpoint}"

        for attempt in range(1, self.max_retries + 1):
            self._rate_limit()
            try:
                response = self.session.request(
                    method, url, timeout=self.timeout, **kwargs
                )

                if response.status_code == 429:
                    wait = min(2 ** attempt * 5, 60)
                    logger.warning(f"Rate limited (429). Waiting {wait}s before retry {attempt}/{self.max_retries}")
                    time.sleep(wait)
                    continue

                # 404 means no results found — return empty dict
                if response.status_code == 404:
                    return {}

                response.raise_for_status()
                return response.json()

            except requests.exceptions.Timeout:
                logger.warning(f"Request timeout (attempt {attempt}/{self.max_retries})")
                if attempt == self.max_retries:
                    raise
                time.sleep(2 ** attempt)

            except requests.exceptions.HTTPError as e:
                if response.status_code >= 500 and attempt < self.max_retries:
                    logger.warning(f"Server error {response.status_code} (attempt {attempt}/{self.max_retries})")
                    time.sleep(2 ** attempt)
                    continue
                raise

        return {}

    def search_design_patents(
        self,
        date_from: date,
        date_to: date,
        keywords: list[str] | None = None,
        limit: int = 25,
    ) -> list[Patent]:
        """Search for granted design patents within a date range.

        Args:
            date_from: Start of grant date range.
            date_to: End of grant date range.
            keywords: Optional keywords to search in title.
            limit: Results per page (max 25).

        Returns:
            List of Patent objects.
        """
        all_patents = []
        offset = 0

        while True:
            q = self._build_query(date_from, date_to, keywords)

            logger.info(f"Searching USPTO API: offset={offset}, date_range={date_from} to {date_to}")
            data = self._request("GET", self.SEARCH_ENDPOINT, params={
                "q": q,
                "rows": limit,
                "start": offset,
                "sort": "applicationMetaData.grantDate desc",
            })

            results = data.get("patentFileWrapperDataBag", [])
            total_count = data.get("count", 0)

            if not results:
                break

            for item in results:
                patent = self._parse_patent(item)
                if patent:
                    all_patents.append(patent)

            offset += limit
            if offset >= total_count:
                break

            logger.info(f"Fetched {len(all_patents)}/{total_count} patents")

        logger.info(f"Total design patents found: {len(all_patents)}")
        return all_patents

    def get_patent_by_number(self, patent_number: str) -> Patent | None:
        """Fetch a single patent by its patent number."""
        q = f"applicationMetaData.patentNumber:{patent_number}"
        try:
            data = self._request("GET", self.SEARCH_ENDPOINT, params={
                "q": q,
                "rows": 1,
            })
            results = data.get("patentFileWrapperDataBag", [])
            if results:
                return self._parse_patent(results[0])
            return None
        except requests.exceptions.HTTPError:
            logger.error(f"Patent not found: {patent_number}")
            return None

    def _build_query(
        self,
        date_from: date,
        date_to: date,
        keywords: list[str] | None,
    ) -> str:
        """Build a Lucene-style query string for the GET search endpoint."""
        parts = [
            "applicationMetaData.applicationTypeLabelName:Design",
            "applicationMetaData.applicationStatusCode:150",
            f"applicationMetaData.grantDate:[{date_from.isoformat()} TO {date_to.isoformat()}]",
        ]

        if keywords:
            keyword_clauses = " OR ".join(
                f'applicationMetaData.inventionTitle:"{kw}"' for kw in keywords
            )
            parts.append(f"({keyword_clauses})")

        return " AND ".join(parts)

    def _parse_patent(self, data: dict) -> Patent | None:
        """Parse API response item into a Patent object.

        The new API nests patent metadata under 'applicationMetaData'.
        """
        try:
            meta = data.get("applicationMetaData", data)

            patent_number = meta.get("patentNumber", "")
            title = meta.get("inventionTitle", "") or ""
            app_number = data.get("applicationNumberText", "") or meta.get("applicationNumberText", "")

            if not patent_number or not title:
                return None

            # Parse grant date (new API uses grantDate instead of patentIssueDate)
            issue_date_str = meta.get("grantDate") or meta.get("patentIssueDate")
            if issue_date_str:
                issue_date = date.fromisoformat(issue_date_str[:10])
            else:
                issue_date = date.today()

            # Parse filing date
            filing_date = None
            filing_date_str = meta.get("filingDate")
            if filing_date_str:
                filing_date = date.fromisoformat(filing_date_str[:10])

            # Parse inventors from inventorBag
            inventors = []
            inventor_bag = meta.get("inventorBag", [])
            if inventor_bag:
                for inv in inventor_bag:
                    name = inv.get("inventorNameText", "")
                    if name:
                        inventors.append(name)
            if not inventors:
                first_inv = meta.get("firstInventorName", "")
                if first_inv:
                    inventors = [first_inv]

            # Parse classifications
            uspc = meta.get("uspcSymbolText", "") or ""
            cpc_bag = meta.get("cpcClassificationBag", [])
            if isinstance(cpc_bag, list):
                cpc = "; ".join(cpc_bag)
            else:
                cpc = str(cpc_bag) if cpc_bag else ""

            # Assignee from applicantBag or firstApplicantName
            assignee = meta.get("firstApplicantName", "") or ""

            return Patent(
                patent_number=patent_number,
                application_number=app_number,
                title=title,
                issue_date=issue_date,
                filing_date=filing_date,
                inventors=inventors,
                assignee=assignee,
                classification_us=uspc,
                classification_cpc=cpc,
                abstract=None,  # Not available in the new API search results
            )

        except Exception as e:
            logger.error(f"Failed to parse patent data: {e}")
            logger.debug(f"Raw data: {json.dumps(data, default=str)[:500]}")
            return None
