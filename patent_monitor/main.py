"""CLI entry point for the patent monitoring tool."""

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config, validate_config
from .db import Database
from .models import Alert
from .notifier import EmailNotifier
from .reporter import export_csv, format_patents_table, print_summary
from .service import run_ai_analysis, run_scan


def setup_logging(level: str = "INFO", log_file: str | None = None):
    """Configure logging."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def cmd_run(args):
    """Execute a single monitoring cycle."""
    config = load_config(args.config)
    setup_logging(config.logging.level, config.logging.file)
    logger = logging.getLogger("patent_monitor")

    errors = validate_config(config)
    if errors:
        for err in errors:
            logger.error(f"Config error: {err}")
        sys.exit(1)

    db = Database(config.database_path)
    with db:
        # --- Run scan ---
        scan_result = run_scan(config, db)

        # --- Run AI analysis on new matches if enabled ---
        if config.ai.enabled and scan_result.alerts:
            patent_numbers = [a.patent.patent_number for a in scan_result.alerts]
            ai_result = run_ai_analysis(config, db, patent_numbers=patent_numbers)

            # Attach AI analysis to alerts
            analysis_map = dict(ai_result.analyzed)
            for alert in scan_result.alerts:
                alert.ai_analysis = analysis_map.get(alert.patent.patent_number)

        # --- Send alerts ---
        notifier = EmailNotifier(config.notifications.email)
        if scan_result.alerts:
            logger.info(f"Sending alerts for {len(scan_result.alerts)} new matching patents")
            success = notifier.send_new_patent_alerts(scan_result.alerts)
            if success:
                for alert in scan_result.alerts:
                    db.mark_notified(alert.patent.patent_number)
            else:
                logger.error("Failed to send email alerts")

        # --- PGR deadline reminders ---
        for threshold in config.notifications.pgr_reminder_months:
            approaching = db.get_patents_approaching_pgr(threshold)
            if approaching:
                logger.info(f"PGR reminder: {len(approaching)} patents within {threshold} months of deadline")
                notifier.send_pgr_reminder(approaching, threshold)

        # --- Summary ---
        total = db.get_patent_count()
        logger.info(f"Run complete. {scan_result.new_matches} new matches. {total} total patents tracked.")

        if scan_result.alerts:
            print(f"\n{scan_result.new_matches} new matching design patents found:")
            print(format_patents_table([a.patent for a in scan_result.alerts]))
        else:
            print("No new matching design patents found.")

        for err in scan_result.errors:
            logger.warning(f"Scan error: {err}")


def cmd_report(args):
    """Generate a report of tracked patents."""
    config = load_config(args.config)
    setup_logging(config.logging.level)

    with Database(config.database_path) as db:
        patents = db.get_all_patents(limit=500)

        if args.format == "csv":
            if args.output:
                export_csv(patents, args.output)
                print(f"CSV exported to {args.output}")
            else:
                print(export_csv(patents))
        elif args.format == "summary":
            print_summary(patents)
        else:
            print(format_patents_table(patents))


def cmd_history(args):
    """Show previously found patents."""
    config = load_config(args.config)
    setup_logging(config.logging.level)

    with Database(config.database_path) as db:
        if args.status:
            patents = db.get_patents_by_status(args.status)
        else:
            patents = db.get_all_patents(limit=args.limit)

        if not patents:
            print("No patents in database.")
            return

        print(format_patents_table(patents))
        print(f"\nTotal: {len(patents)} patents")


def cmd_test_email(args):
    """Send a test email to verify configuration."""
    config = load_config(args.config)
    setup_logging(config.logging.level)

    errors = validate_config(config)
    email_errors = [e for e in errors if "SMTP" in e or "email" in e.lower() or "recipient" in e.lower()]
    if email_errors:
        for err in email_errors:
            print(f"Error: {err}")
        sys.exit(1)

    notifier = EmailNotifier(config.notifications.email)
    success = notifier.send_test_email()

    if success:
        print(f"Test email sent to: {', '.join(config.notifications.email.recipients)}")
    else:
        print("Failed to send test email. Check your SMTP settings.")
        sys.exit(1)


def cmd_init_db(args):
    """Initialize the database."""
    config = load_config(args.config)
    setup_logging(config.logging.level)

    db = Database(config.database_path)
    db.init_db()
    db.close()
    print(f"Database initialized at {config.database_path}")


def main():
    parser = argparse.ArgumentParser(
        prog="patent-monitor",
        description="Monitor USPTO design patents and track PGR deadlines.",
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run
    run_parser = subparsers.add_parser("run", help="Execute a monitoring cycle")
    run_parser.set_defaults(func=cmd_run)

    # report
    report_parser = subparsers.add_parser("report", help="Generate a report")
    report_parser.add_argument(
        "--format", choices=["table", "csv", "summary"], default="table",
        help="Output format (default: table)",
    )
    report_parser.add_argument(
        "--output", "-o", help="Output file path (for CSV)",
    )
    report_parser.set_defaults(func=cmd_report)

    # history
    history_parser = subparsers.add_parser("history", help="Show tracked patents")
    history_parser.add_argument(
        "--status", choices=["new", "reviewed", "flagged", "dismissed"],
        help="Filter by status",
    )
    history_parser.add_argument(
        "--limit", type=int, default=50,
        help="Maximum number of patents to show (default: 50)",
    )
    history_parser.set_defaults(func=cmd_history)

    # test-email
    test_parser = subparsers.add_parser("test-email", help="Send a test email")
    test_parser.set_defaults(func=cmd_test_email)

    # init-db
    init_parser = subparsers.add_parser("init-db", help="Initialize the database")
    init_parser.set_defaults(func=cmd_init_db)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
