"""Patent matching logic against configured search criteria."""

import logging
import re

from .config import SearchCriteriaConfig
from .models import Patent

logger = logging.getLogger(__name__)


class PatentMatcher:
    """Determines if a patent matches configured search criteria."""

    def __init__(self, criteria_list: list[SearchCriteriaConfig]):
        self.criteria_list = criteria_list

    def match(self, patent: Patent) -> list[str]:
        """Check if a patent matches any configured criteria.

        Args:
            patent: The patent to check.

        Returns:
            List of matched criteria descriptions. Empty if no match.
        """
        all_matches = []

        for criteria in self.criteria_list:
            matches = self._match_single(patent, criteria)
            if matches:
                all_matches.extend(matches)

        return all_matches

    def _match_single(self, patent: Patent, criteria: SearchCriteriaConfig) -> list[str]:
        """Check a patent against a single set of criteria."""
        # Check exclusions first
        if self._is_excluded(patent, criteria):
            return []

        matches = []

        # Check US classification codes
        for us_class in criteria.us_classes:
            if self._class_matches(patent.classification_us, us_class):
                matches.append(f"US class: {us_class} (criteria: {criteria.name})")

        # Check CPC classification codes
        for cpc_class in criteria.cpc_classes:
            if self._class_matches(patent.classification_cpc, cpc_class):
                matches.append(f"CPC class: {cpc_class} (criteria: {criteria.name})")

        # Check keywords in title and abstract
        for keyword in criteria.keywords:
            if self._keyword_matches(patent, keyword):
                matches.append(f"Keyword: '{keyword}' (criteria: {criteria.name})")

        return matches

    def _class_matches(self, patent_classification: str, target_class: str) -> bool:
        """Check if a patent's classification matches a target class prefix.

        Handles cases like:
        - patent_classification="D16/300" matches target_class="D16/300" (exact)
        - patent_classification="D16/300" matches target_class="D16/3" (prefix)
        - patent_classification="D16/300" matches target_class="D16" (prefix)
        - patent_classification="G02C 1/00; G02C 5/00" matches target_class="G02C" (prefix in multi-value)
        """
        if not patent_classification or not target_class:
            return False

        # Normalize: remove extra spaces, split on semicolons for multi-value fields
        target_normalized = target_class.strip().upper().replace(" ", "")
        classifications = [c.strip().upper().replace(" ", "") for c in patent_classification.split(";")]

        for cls in classifications:
            if cls.startswith(target_normalized):
                return True

        return False

    def _keyword_matches(self, patent: Patent, keyword: str) -> bool:
        """Check if a keyword appears in the patent title or abstract (case-insensitive)."""
        keyword_lower = keyword.lower()
        pattern = re.compile(re.escape(keyword_lower), re.IGNORECASE)

        if patent.title and pattern.search(patent.title):
            return True
        if patent.abstract and pattern.search(patent.abstract):
            return True

        return False

    def _is_excluded(self, patent: Patent, criteria: SearchCriteriaConfig) -> bool:
        """Check if a patent should be excluded based on assignee."""
        if not criteria.assignee_exclude or not patent.assignee:
            return False

        assignee_lower = patent.assignee.lower()
        for excluded in criteria.assignee_exclude:
            if excluded.lower() in assignee_lower:
                logger.debug(f"Excluding patent {patent.patent_number}: assignee '{patent.assignee}' matches exclusion '{excluded}'")
                return True

        return False
