"""Tests for data models."""

from datetime import date
from freezegun import freeze_time

from patent_monitor.models import Patent


def make_patent(**kwargs) -> Patent:
    defaults = {
        "patent_number": "D1012345",
        "title": "Eyeglasses Frame",
        "issue_date": date(2026, 1, 15),
    }
    defaults.update(kwargs)
    return Patent(**defaults)


def test_pgr_deadline():
    p = make_patent(issue_date=date(2026, 1, 15))
    assert p.pgr_deadline == date(2026, 10, 15)


def test_pgr_deadline_month_boundary():
    p = make_patent(issue_date=date(2026, 5, 31))
    # 9 months from May 31 -> Feb 28 (2027 is not a leap year)
    assert p.pgr_deadline == date(2027, 2, 28)


@freeze_time("2026-02-24")
def test_pgr_months_remaining():
    p = make_patent(issue_date=date(2026, 1, 15))
    # PGR deadline is Oct 15, 2026. From Feb 24 that's ~7.7 months
    remaining = p.pgr_months_remaining
    assert 7.0 < remaining < 8.0


@freeze_time("2026-02-24")
def test_urgency_low():
    p = make_patent(issue_date=date(2026, 1, 15))
    assert p.urgency == "low"


@freeze_time("2026-08-01")
def test_urgency_medium():
    p = make_patent(issue_date=date(2026, 1, 15))
    # PGR deadline Oct 15. From Aug 1 that's ~2.5 months
    assert p.urgency == "high"


@freeze_time("2027-01-01")
def test_urgency_expired():
    p = make_patent(issue_date=date(2026, 1, 15))
    # PGR deadline was Oct 15, 2026
    assert p.urgency == "expired"


def test_uspto_url_with_d_prefix():
    p = make_patent(patent_number="D1012345")
    assert p.uspto_url == "https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/D1012345"


def test_uspto_url_without_d_prefix():
    p = make_patent(patent_number="1012345")
    assert p.uspto_url == "https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/D1012345"
