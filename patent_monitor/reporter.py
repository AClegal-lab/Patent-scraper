"""Report generation for tracked patents."""

import csv
import io
import logging
from datetime import date

from .models import Patent

logger = logging.getLogger(__name__)


def format_patents_table(patents: list[Patent]) -> str:
    """Format patents as a console-friendly table.

    Args:
        patents: List of patents to display.

    Returns:
        Formatted string table.
    """
    if not patents:
        return "No patents found."

    # Column widths
    num_w = 12
    title_w = 40
    date_w = 12
    assignee_w = 25
    class_w = 12
    urgency_w = 8

    header = (
        f"{'Patent #':<{num_w}} "
        f"{'Title':<{title_w}} "
        f"{'Issue Date':<{date_w}} "
        f"{'PGR Deadline':<{date_w}} "
        f"{'Assignee':<{assignee_w}} "
        f"{'US Class':<{class_w}} "
        f"{'Urgency':<{urgency_w}} "
        f"{'Status'}"
    )
    separator = "-" * len(header)

    rows = [header, separator]
    for p in patents:
        title = p.title[:title_w - 3] + "..." if len(p.title) > title_w else p.title
        assignee = p.assignee[:assignee_w - 3] + "..." if len(p.assignee) > assignee_w else p.assignee
        us_class = p.classification_us[:class_w] if p.classification_us else ""

        row = (
            f"{p.patent_number:<{num_w}} "
            f"{title:<{title_w}} "
            f"{p.issue_date.isoformat():<{date_w}} "
            f"{p.pgr_deadline.isoformat():<{date_w}} "
            f"{assignee:<{assignee_w}} "
            f"{us_class:<{class_w}} "
            f"{p.urgency:<{urgency_w}} "
            f"{p.status}"
        )
        rows.append(row)

    return "\n".join(rows)


def export_csv(patents: list[Patent], file_path: str | None = None) -> str:
    """Export patents to CSV format.

    Args:
        patents: List of patents to export.
        file_path: Optional file path to write to. If None, returns CSV as string.

    Returns:
        CSV string if no file_path, otherwise the file path written to.
    """
    fieldnames = [
        "patent_number",
        "title",
        "issue_date",
        "pgr_deadline",
        "pgr_months_remaining",
        "urgency",
        "filing_date",
        "inventors",
        "assignee",
        "classification_us",
        "classification_cpc",
        "status",
        "abstract",
        "uspto_url",
    ]

    output = io.StringIO() if file_path is None else open(file_path, "w", newline="")

    try:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for p in patents:
            writer.writerow({
                "patent_number": p.patent_number,
                "title": p.title,
                "issue_date": p.issue_date.isoformat(),
                "pgr_deadline": p.pgr_deadline.isoformat(),
                "pgr_months_remaining": f"{p.pgr_months_remaining:.1f}",
                "urgency": p.urgency,
                "filing_date": p.filing_date.isoformat() if p.filing_date else "",
                "inventors": "; ".join(p.inventors),
                "assignee": p.assignee,
                "classification_us": p.classification_us,
                "classification_cpc": p.classification_cpc,
                "status": p.status,
                "abstract": p.abstract or "",
                "uspto_url": p.uspto_url,
            })

        if file_path is None:
            return output.getvalue()
        else:
            logger.info(f"CSV exported to {file_path}")
            return file_path
    finally:
        if file_path is not None:
            output.close()


def print_summary(patents: list[Patent]):
    """Print a summary of patent statistics to stdout."""
    if not patents:
        print("No patents in database.")
        return

    total = len(patents)
    by_status = {}
    by_urgency = {}

    for p in patents:
        by_status[p.status] = by_status.get(p.status, 0) + 1
        by_urgency[p.urgency] = by_urgency.get(p.urgency, 0) + 1

    print(f"\n=== Patent Monitor Summary ({date.today().isoformat()}) ===\n")
    print(f"Total tracked patents: {total}")
    print()

    print("By status:")
    for status, count in sorted(by_status.items()):
        print(f"  {status}: {count}")
    print()

    print("By PGR urgency:")
    for urgency in ["high", "medium", "low", "expired"]:
        if urgency in by_urgency:
            print(f"  {urgency}: {by_urgency[urgency]}")
    print()

    # Show upcoming PGR deadlines
    approaching = [p for p in patents if p.urgency in ("high", "medium") and p.status == "flagged"]
    if approaching:
        approaching.sort(key=lambda p: p.pgr_deadline)
        print("Upcoming PGR deadlines (flagged patents):")
        for p in approaching[:10]:
            print(f"  {p.patent_number} — {p.pgr_deadline.isoformat()} ({p.pgr_months_remaining:.1f} months) — {p.title[:50]}")
        print()
