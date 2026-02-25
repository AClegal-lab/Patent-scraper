"""Tests for database operations."""

from datetime import date, datetime

from patent_monitor.db import Database
from patent_monitor.models import Patent, SearchRun


def make_patent(**kwargs) -> Patent:
    defaults = {
        "patent_number": "D1012345",
        "title": "Eyeglasses Frame",
        "issue_date": date(2026, 2, 18),
        "application_number": "29/900001",
        "assignee": "Acme Eyewear Inc.",
        "classification_us": "D16/300",
    }
    defaults.update(kwargs)
    return Patent(**defaults)


def get_db() -> Database:
    db = Database(":memory:")
    db.init_db()
    return db


def test_init_db():
    db = get_db()
    # Should not raise
    db.init_db()
    db.close()


def test_insert_and_get_patent():
    db = get_db()
    p = make_patent()
    assert db.insert_patent(p, ["US class: D16/300"]) is True

    retrieved = db.get_patent("D1012345")
    assert retrieved is not None
    assert retrieved.patent_number == "D1012345"
    assert retrieved.title == "Eyeglasses Frame"
    assert retrieved.assignee == "Acme Eyewear Inc."
    db.close()


def test_insert_duplicate():
    db = get_db()
    p = make_patent()
    assert db.insert_patent(p) is True
    assert db.insert_patent(p) is False  # duplicate
    db.close()


def test_patent_exists():
    db = get_db()
    assert db.patent_exists("D1012345") is False
    db.insert_patent(make_patent())
    assert db.patent_exists("D1012345") is True
    db.close()


def test_get_new_patents():
    db = get_db()
    db.insert_patent(make_patent(patent_number="D001", title="A"))
    db.insert_patent(make_patent(patent_number="D002", title="B"))

    new = db.get_new_patents()
    assert len(new) == 2
    db.close()


def test_update_patent_status():
    db = get_db()
    db.insert_patent(make_patent())
    db.update_patent_status("D1012345", "flagged")

    p = db.get_patent("D1012345")
    assert p.status == "flagged"
    db.close()


def test_mark_notified():
    db = get_db()
    db.insert_patent(make_patent())
    db.mark_notified("D1012345")

    p = db.get_patent("D1012345")
    assert p.notified_at is not None
    db.close()


def test_get_patent_count():
    db = get_db()
    assert db.get_patent_count() == 0
    db.insert_patent(make_patent(patent_number="D001", title="A"))
    db.insert_patent(make_patent(patent_number="D002", title="B"))
    assert db.get_patent_count() == 2
    db.close()


def test_log_search_run_and_get_last_date():
    db = get_db()
    run = SearchRun(
        run_at=datetime(2026, 2, 20, 10, 0),
        source="api",
        results_count=5,
        new_matches_count=2,
    )
    db.log_search_run(run)

    last = db.get_last_run_date("api")
    assert last == date(2026, 2, 20)
    db.close()


def test_get_last_run_date_none():
    db = get_db()
    assert db.get_last_run_date("api") is None
    db.close()


def test_get_patents_by_status():
    db = get_db()
    db.insert_patent(make_patent(patent_number="D001", title="A"))
    db.insert_patent(make_patent(patent_number="D002", title="B"))
    db.update_patent_status("D001", "flagged")

    flagged = db.get_patents_by_status("flagged")
    assert len(flagged) == 1
    assert flagged[0].patent_number == "D001"

    new = db.get_patents_by_status("new")
    assert len(new) == 1
    assert new[0].patent_number == "D002"
    db.close()


def test_context_manager():
    with Database(":memory:") as db:
        db.insert_patent(make_patent())
        assert db.get_patent_count() == 1
