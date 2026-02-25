"""Data models for the patent monitoring tool."""

from dataclasses import dataclass, field
from datetime import date, datetime

from dateutil.relativedelta import relativedelta


@dataclass
class Patent:
    patent_number: str
    title: str
    issue_date: date
    application_number: str = ""
    filing_date: date | None = None
    inventors: list[str] = field(default_factory=list)
    assignee: str = ""
    classification_us: str = ""
    classification_cpc: str = ""
    classification_locarno: str = ""
    image_url: str | None = None
    abstract: str | None = None
    status: str = "new"  # new | reviewed | flagged | dismissed
    first_seen: datetime = field(default_factory=datetime.now)
    notified_at: datetime | None = None
    notes: str | None = None

    @property
    def pgr_deadline(self) -> date:
        """Post-Grant Review deadline: 9 months after issue date."""
        return self.issue_date + relativedelta(months=9)

    @property
    def pgr_months_remaining(self) -> float:
        """Months remaining until PGR deadline."""
        delta = relativedelta(self.pgr_deadline, date.today())
        return delta.months + delta.days / 30.0

    @property
    def urgency(self) -> str:
        """Urgency based on PGR deadline proximity."""
        remaining = self.pgr_months_remaining
        if remaining <= 0:
            return "expired"
        elif remaining < 3:
            return "high"
        elif remaining < 6:
            return "medium"
        return "low"

    @property
    def uspto_url(self) -> str:
        """Link to the patent PDF on USPTO PPUBS."""
        num = self.patent_number.replace(",", "")
        if not num.startswith("D"):
            num = f"D{num}"
        return f"https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/{num}"


@dataclass
class SearchCriteria:
    name: str
    us_classes: list[str] = field(default_factory=list)
    cpc_classes: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    assignee_exclude: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """AI-powered design similarity analysis result."""
    similarity_score: int       # 0-100
    risk_level: str             # "high" | "medium" | "low" | "none"
    recommendation: str         # "flag" | "monitor" | "dismiss"
    reasoning: str              # Claude's explanation
    patent_image_used: bool = False
    product_images_used: list[str] = field(default_factory=list)
    model_used: str = ""
    analyzed_at: datetime = field(default_factory=datetime.now)
    error: str | None = None


@dataclass
class Alert:
    patent: Patent
    matched_criteria: list[str] = field(default_factory=list)
    criteria_name: str = ""
    sent_at: datetime | None = None
    ai_analysis: AnalysisResult | None = None


@dataclass
class SearchRun:
    run_at: datetime
    source: str  # "api" | "gazette"
    query_params: str = ""
    results_count: int = 0
    new_matches_count: int = 0
    error: str | None = None
    duration_seconds: float = 0.0
