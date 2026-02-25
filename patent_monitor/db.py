"""SQLite database operations for patent tracking."""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .models import Patent, SearchRun

SCHEMA = """
CREATE TABLE IF NOT EXISTS patents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patent_number TEXT UNIQUE NOT NULL,
    application_number TEXT,
    title TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    filing_date TEXT,
    inventors TEXT,
    assignee TEXT,
    classification_us TEXT,
    classification_cpc TEXT,
    classification_locarno TEXT,
    image_url TEXT,
    abstract TEXT,
    pgr_deadline TEXT NOT NULL,
    status TEXT DEFAULT 'new',
    matched_criteria TEXT,
    first_seen_at TEXT NOT NULL,
    notified_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS search_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    source TEXT NOT NULL,
    query_params TEXT,
    results_count INTEGER,
    new_matches_count INTEGER,
    error TEXT,
    duration_seconds REAL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patent_id INTEGER REFERENCES patents(id),
    notification_type TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    recipient TEXT NOT NULL,
    status TEXT DEFAULT 'sent',
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_patents_issue_date ON patents(issue_date);
CREATE INDEX IF NOT EXISTS idx_patents_status ON patents(status);
CREATE INDEX IF NOT EXISTS idx_patents_pgr_deadline ON patents(pgr_deadline);
CREATE INDEX IF NOT EXISTS idx_patents_classification_us ON patents(classification_us);
CREATE INDEX IF NOT EXISTS idx_search_runs_run_at ON search_runs(run_at);
"""


class Database:
    def __init__(self, db_path: str = "data/patents.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def init_db(self):
        """Create tables and indexes."""
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Run schema migrations for new columns."""
        cursor = self.conn.execute("PRAGMA table_info(patents)")
        columns = [row[1] for row in cursor.fetchall()]
        if "ai_analysis" not in columns:
            self.conn.execute("ALTER TABLE patents ADD COLUMN ai_analysis TEXT")

    def close(self):
        self.conn.close()

    def __enter__(self):
        self.init_db()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # --- Patent operations ---

    def patent_exists(self, patent_number: str) -> bool:
        """Check if a patent is already in the database."""
        cur = self.conn.execute(
            "SELECT 1 FROM patents WHERE patent_number = ?", (patent_number,)
        )
        return cur.fetchone() is not None

    def insert_patent(self, patent: Patent, matched_criteria: list[str] | None = None) -> bool:
        """Insert a patent. Returns True if inserted, False if duplicate."""
        try:
            self.conn.execute(
                """INSERT INTO patents (
                    patent_number, application_number, title, issue_date,
                    filing_date, inventors, assignee, classification_us,
                    classification_cpc, classification_locarno, image_url,
                    abstract, pgr_deadline, status, matched_criteria,
                    first_seen_at, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    patent.patent_number,
                    patent.application_number,
                    patent.title,
                    patent.issue_date.isoformat(),
                    patent.filing_date.isoformat() if patent.filing_date else None,
                    json.dumps(patent.inventors),
                    patent.assignee,
                    patent.classification_us,
                    patent.classification_cpc,
                    patent.classification_locarno,
                    patent.image_url,
                    patent.abstract,
                    patent.pgr_deadline.isoformat(),
                    patent.status,
                    json.dumps(matched_criteria) if matched_criteria else None,
                    patent.first_seen.isoformat(),
                    patent.notes,
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_patent(self, patent_number: str) -> Patent | None:
        """Get a single patent by number."""
        cur = self.conn.execute(
            "SELECT * FROM patents WHERE patent_number = ?", (patent_number,)
        )
        row = cur.fetchone()
        return self._row_to_patent(row) if row else None

    def get_new_patents(self) -> list[Patent]:
        """Get all patents with status 'new' (not yet notified)."""
        cur = self.conn.execute(
            "SELECT * FROM patents WHERE status = 'new' ORDER BY issue_date DESC"
        )
        return [self._row_to_patent(row) for row in cur.fetchall()]

    def get_patents_by_status(self, status: str) -> list[Patent]:
        """Get patents by status."""
        cur = self.conn.execute(
            "SELECT * FROM patents WHERE status = ? ORDER BY issue_date DESC",
            (status,),
        )
        return [self._row_to_patent(row) for row in cur.fetchall()]

    def get_patents_approaching_pgr(self, months_remaining: float) -> list[Patent]:
        """Get flagged patents whose PGR deadline is within N months."""
        deadline_cutoff = date.today()
        cur = self.conn.execute(
            """SELECT * FROM patents
               WHERE status = 'flagged'
               AND pgr_deadline >= ?
               ORDER BY pgr_deadline ASC""",
            (deadline_cutoff.isoformat(),),
        )
        patents = [self._row_to_patent(row) for row in cur.fetchall()]
        return [p for p in patents if p.pgr_months_remaining <= months_remaining]

    def get_all_patents(self, limit: int = 100, offset: int = 0) -> list[Patent]:
        """Get all patents with pagination."""
        cur = self.conn.execute(
            "SELECT * FROM patents ORDER BY issue_date DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._row_to_patent(row) for row in cur.fetchall()]

    def get_patents_by_date_range(self, date_from: date, date_to: date, limit: int = 100) -> list[Patent]:
        """Get patents with issue_date within the given range."""
        cur = self.conn.execute(
            "SELECT * FROM patents WHERE issue_date >= ? AND issue_date <= ? ORDER BY issue_date DESC LIMIT ?",
            (date_from.isoformat(), date_to.isoformat(), limit),
        )
        return [self._row_to_patent(row) for row in cur.fetchall()]

    def update_patent_status(self, patent_number: str, status: str):
        """Update the status of a patent."""
        self.conn.execute(
            "UPDATE patents SET status = ? WHERE patent_number = ?",
            (status, patent_number),
        )
        self.conn.commit()

    def mark_notified(self, patent_number: str):
        """Mark a patent as notified."""
        self.conn.execute(
            "UPDATE patents SET notified_at = ? WHERE patent_number = ?",
            (datetime.now().isoformat(), patent_number),
        )
        self.conn.commit()

    def get_patent_count(self) -> int:
        """Get total number of patents in the database."""
        cur = self.conn.execute("SELECT COUNT(*) FROM patents")
        return cur.fetchone()[0]

    def update_ai_analysis(self, patent_number: str, analysis_json: str):
        """Store AI analysis results for a patent."""
        self.conn.execute(
            "UPDATE patents SET ai_analysis = ? WHERE patent_number = ?",
            (analysis_json, patent_number),
        )
        self.conn.commit()

    def get_ai_analysis(self, patent_number: str) -> str | None:
        """Get AI analysis JSON for a patent."""
        cur = self.conn.execute(
            "SELECT ai_analysis FROM patents WHERE patent_number = ?",
            (patent_number,),
        )
        row = cur.fetchone()
        return row["ai_analysis"] if row else None

    def get_patents_without_ai_analysis(self) -> list[Patent]:
        """Get patents that haven't been AI-analyzed yet."""
        cur = self.conn.execute(
            "SELECT * FROM patents WHERE ai_analysis IS NULL ORDER BY issue_date DESC"
        )
        return [self._row_to_patent(row) for row in cur.fetchall()]

    def get_patent_count_by_status(self) -> dict[str, int]:
        """Get patent counts grouped by status."""
        cur = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM patents GROUP BY status"
        )
        return {row["status"]: row["cnt"] for row in cur.fetchall()}

    def get_recent_search_runs(self, limit: int = 10) -> list[dict]:
        """Get recent search runs for dashboard display."""
        cur = self.conn.execute(
            """SELECT run_at, source, results_count, new_matches_count,
                      error, duration_seconds
               FROM search_runs
               ORDER BY run_at DESC LIMIT ?""",
            (limit,),
        )
        return [
            {
                "run_at": row["run_at"],
                "source": row["source"],
                "results_count": row["results_count"],
                "new_matches_count": row["new_matches_count"],
                "error": row["error"],
                "duration_seconds": row["duration_seconds"],
            }
            for row in cur.fetchall()
        ]

    # --- Search run operations ---

    def log_search_run(self, run: SearchRun):
        """Log a search run."""
        self.conn.execute(
            """INSERT INTO search_runs (
                run_at, source, query_params, results_count,
                new_matches_count, error, duration_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                run.run_at.isoformat(),
                run.source,
                run.query_params,
                run.results_count,
                run.new_matches_count,
                run.error,
                run.duration_seconds,
            ),
        )
        self.conn.commit()

    def get_last_run_date(self, source: str = "api") -> date | None:
        """Get the date of the last successful search run for a source."""
        cur = self.conn.execute(
            """SELECT run_at FROM search_runs
               WHERE source = ? AND error IS NULL
               ORDER BY run_at DESC LIMIT 1""",
            (source,),
        )
        row = cur.fetchone()
        if row:
            return datetime.fromisoformat(row["run_at"]).date()
        return None

    # --- Notification operations ---

    def log_notification(self, patent_id: int, notification_type: str,
                         recipient: str, status: str = "sent", error: str | None = None):
        """Log a sent notification."""
        self.conn.execute(
            """INSERT INTO notifications (
                patent_id, notification_type, sent_at, recipient, status, error
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (patent_id, notification_type, datetime.now().isoformat(), recipient, status, error),
        )
        self.conn.commit()

    # --- Helpers ---

    def _row_to_patent(self, row: sqlite3.Row) -> Patent:
        """Convert a database row to a Patent object."""
        return Patent(
            patent_number=row["patent_number"],
            application_number=row["application_number"] or "",
            title=row["title"],
            issue_date=date.fromisoformat(row["issue_date"]),
            filing_date=date.fromisoformat(row["filing_date"]) if row["filing_date"] else None,
            inventors=json.loads(row["inventors"]) if row["inventors"] else [],
            assignee=row["assignee"] or "",
            classification_us=row["classification_us"] or "",
            classification_cpc=row["classification_cpc"] or "",
            classification_locarno=row["classification_locarno"] or "",
            image_url=row["image_url"],
            abstract=row["abstract"],
            status=row["status"] or "new",
            first_seen=datetime.fromisoformat(row["first_seen_at"]),
            notified_at=datetime.fromisoformat(row["notified_at"]) if row["notified_at"] else None,
            notes=row["notes"],
        )
