"""Tests for patent matching logic."""

from datetime import date

from patent_monitor.config import SearchCriteriaConfig
from patent_monitor.matcher import PatentMatcher
from patent_monitor.models import Patent


def make_patent(**kwargs) -> Patent:
    defaults = {
        "patent_number": "D1012345",
        "title": "Eyeglasses Frame",
        "issue_date": date(2026, 2, 18),
        "classification_us": "D16/300",
        "classification_cpc": "G02C 1/00",
    }
    defaults.update(kwargs)
    return Patent(**defaults)


def make_criteria(**kwargs) -> SearchCriteriaConfig:
    defaults = {
        "name": "Eyewear",
        "us_classes": ["D16/300", "D16/301", "D16/302"],
        "cpc_classes": ["G02C"],
        "keywords": ["eyeglasses", "sunglasses"],
        "assignee_exclude": [],
    }
    defaults.update(kwargs)
    return SearchCriteriaConfig(**defaults)


def test_us_class_exact_match():
    matcher = PatentMatcher([make_criteria()])
    patent = make_patent(classification_us="D16/300")
    matches = matcher.match(patent)
    assert any("D16/300" in m for m in matches)


def test_us_class_prefix_match():
    matcher = PatentMatcher([make_criteria(us_classes=["D16/3"])])
    patent = make_patent(classification_us="D16/300")
    matches = matcher.match(patent)
    assert any("D16/3" in m for m in matches)


def test_us_class_no_match():
    matcher = PatentMatcher([make_criteria(us_classes=["D26/100"])])
    patent = make_patent(classification_us="D16/300", classification_cpc="")
    matches = matcher.match(patent)
    # Should still match on CPC or keywords from defaults
    # Let's be explicit: no CPC, no keywords
    matcher2 = PatentMatcher([make_criteria(us_classes=["D26/100"], cpc_classes=[], keywords=[])])
    matches2 = matcher2.match(patent)
    assert len(matches2) == 0


def test_cpc_class_match():
    matcher = PatentMatcher([make_criteria(us_classes=[], keywords=[])])
    patent = make_patent(classification_cpc="G02C 1/00")
    matches = matcher.match(patent)
    assert any("G02C" in m for m in matches)


def test_cpc_multi_value_match():
    matcher = PatentMatcher([make_criteria(us_classes=[], cpc_classes=["G02C 5/"], keywords=[])])
    patent = make_patent(classification_cpc="G02C 1/00; G02C 5/00")
    matches = matcher.match(patent)
    assert len(matches) > 0


def test_keyword_match_title():
    matcher = PatentMatcher([make_criteria(us_classes=[], cpc_classes=[])])
    patent = make_patent(
        title="Modern Sunglasses Design",
        classification_us="",
        classification_cpc="",
    )
    matches = matcher.match(patent)
    assert any("sunglasses" in m.lower() for m in matches)


def test_keyword_match_abstract():
    matcher = PatentMatcher([make_criteria(us_classes=[], cpc_classes=[])])
    patent = make_patent(
        title="Frame Design",
        abstract="An ornamental design for eyeglasses.",
        classification_us="",
        classification_cpc="",
    )
    matches = matcher.match(patent)
    assert any("eyeglasses" in m.lower() for m in matches)


def test_keyword_case_insensitive():
    matcher = PatentMatcher([make_criteria(us_classes=[], cpc_classes=[], keywords=["EYEGLASSES"])])
    patent = make_patent(
        title="eyeglasses frame",
        classification_us="",
        classification_cpc="",
    )
    matches = matcher.match(patent)
    assert len(matches) > 0


def test_no_match():
    matcher = PatentMatcher([make_criteria()])
    patent = make_patent(
        title="Table Lamp",
        classification_us="D26/100",
        classification_cpc="F21S 8/00",
        abstract="An ornamental design for a lamp.",
    )
    matches = matcher.match(patent)
    assert len(matches) == 0


def test_assignee_exclusion():
    matcher = PatentMatcher([make_criteria(assignee_exclude=["Acme Eyewear"])])
    patent = make_patent(assignee="Acme Eyewear Inc.")
    matches = matcher.match(patent)
    assert len(matches) == 0


def test_assignee_exclusion_case_insensitive():
    matcher = PatentMatcher([make_criteria(assignee_exclude=["acme eyewear"])])
    patent = make_patent(assignee="ACME EYEWEAR INC.")
    matches = matcher.match(patent)
    assert len(matches) == 0


def test_multiple_criteria():
    criteria1 = make_criteria(name="Eyewear", us_classes=["D16/300"], cpc_classes=[], keywords=[])
    criteria2 = make_criteria(name="Optical", us_classes=[], cpc_classes=["G02C"], keywords=[])

    matcher = PatentMatcher([criteria1, criteria2])
    patent = make_patent(classification_us="D16/300", classification_cpc="G02C 1/00")
    matches = matcher.match(patent)
    assert len(matches) == 2


def test_empty_classifications():
    matcher = PatentMatcher([make_criteria(us_classes=[], cpc_classes=[], keywords=["eyeglasses"])])
    patent = make_patent(
        title="Eyeglasses",
        classification_us="",
        classification_cpc="",
    )
    matches = matcher.match(patent)
    assert len(matches) > 0
