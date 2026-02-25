"""Service layer — reusable business logic for both CLI and Web UI."""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .analyzer import PatentAnalyzer
from .api.gazette_scraper import GazetteScraper
from .api.uspto_client import USPTOClient
from .config import Config
from .db import Database
from .image_fetcher import PatentImageFetcher, load_product_images
from .matcher import PatentMatcher
from .models import Alert, AnalysisResult, Patent, SearchRun

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Result of a full scan cycle."""
    alerts: list[Alert] = field(default_factory=list)
    total_fetched: int = 0
    new_matches: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class AiAnalysisResult:
    """Result of an AI analysis batch."""
    analyzed: list[tuple[str, AnalysisResult]] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


def run_scan(
    config: Config,
    db: Database,
    date_from: date | None = None,
    date_to: date | None = None,
    progress_callback=None,
) -> ScanResult:
    """Run a full patent scan cycle (API + Gazette).

    Args:
        config: Application configuration.
        db: Database instance (must be initialized).
        date_from: Start date for search (overrides auto-detection from last run).
        date_to: End date for search (defaults to today).
        progress_callback: Optional callable(message: str) for progress updates.

    Returns:
        ScanResult with alerts and statistics.
    """
    result = ScanResult()
    start_time = time.time()

    matcher = PatentMatcher(config.search_criteria)

    # --- USPTO API search ---
    if config.sources.uspto_api:
        if progress_callback:
            progress_callback("Searching USPTO API...")
        api_alerts, api_fetched, api_error = _search_api(
            config, db, matcher, date_from=date_from, date_to=date_to
        )
        result.alerts.extend(api_alerts)
        result.total_fetched += api_fetched
        result.new_matches += len(api_alerts)
        if api_error:
            result.errors.append(api_error)

    # --- Official Gazette scrape ---
    if config.sources.official_gazette:
        if progress_callback:
            progress_callback("Scraping Official Gazette...")
        gaz_alerts, gaz_fetched, gaz_error = _search_gazette(db, matcher)
        result.alerts.extend(gaz_alerts)
        result.total_fetched += gaz_fetched
        result.new_matches += len(gaz_alerts)
        if gaz_error:
            result.errors.append(gaz_error)

    result.duration_seconds = time.time() - start_time

    if progress_callback:
        progress_callback(
            f"Scan complete: {result.total_fetched} patents fetched, "
            f"{result.new_matches} new matches"
        )

    logger.info(
        f"Scan complete. {result.total_fetched} fetched, "
        f"{result.new_matches} new matches in {result.duration_seconds:.1f}s"
    )

    return result


def run_ai_analysis(
    config: Config,
    db: Database,
    patent_numbers: list[str] | None = None,
    progress_callback=None,
) -> AiAnalysisResult:
    """Run AI analysis on patents.

    Args:
        config: Application configuration (must have ai.enabled=True).
        db: Database instance (must be initialized).
        patent_numbers: Specific patents to analyze, or None for all unanalyzed.
        progress_callback: Optional callable(message: str) for progress updates.

    Returns:
        AiAnalysisResult with analysis details.
    """
    result = AiAnalysisResult()
    start_time = time.time()

    if not config.ai.enabled:
        result.errors.append("AI analysis is not enabled in configuration")
        return result

    # Initialize components
    analyzer = PatentAnalyzer(config.ai)
    image_fetcher = PatentImageFetcher(timeout=config.api.timeout_seconds)
    product_images = load_product_images(
        config.ai.product_images_dir,
        max_images=config.ai.max_product_images,
    )

    if not product_images:
        result.errors.append("No product images found. Add images to the product images directory.")
        return result

    if progress_callback:
        progress_callback(f"Loaded {len(product_images)} product images")

    # Get patents to analyze
    if patent_numbers:
        patents = [db.get_patent(pn) for pn in patent_numbers]
        patents = [p for p in patents if p is not None]
    else:
        patents = db.get_patents_without_ai_analysis()

    if not patents:
        if progress_callback:
            progress_callback("No patents to analyze")
        result.duration_seconds = time.time() - start_time
        return result

    # Analyze each patent
    for i, patent in enumerate(patents):
        if progress_callback:
            progress_callback(f"Analyzing {patent.patent_number} ({i+1}/{len(patents)})...")

        try:
            analysis = _analyze_single_patent(
                patent, analyzer, image_fetcher, product_images, db
            )
            if analysis:
                result.analyzed.append((patent.patent_number, analysis))
                logger.info(
                    f"AI analysis for {patent.patent_number}: "
                    f"score={analysis.similarity_score}%, "
                    f"risk={analysis.risk_level}, "
                    f"rec={analysis.recommendation}"
                )
            else:
                result.skipped += 1
        except Exception as e:
            logger.error(f"AI analysis failed for {patent.patent_number}: {e}")
            result.errors.append(f"{patent.patent_number}: {e}")

    result.duration_seconds = time.time() - start_time

    if progress_callback:
        progress_callback(
            f"AI analysis complete: {len(result.analyzed)} analyzed, "
            f"{result.skipped} skipped"
        )

    return result


def get_dashboard_stats(db: Database) -> dict:
    """Get statistics for the web dashboard.

    Returns:
        Dict with keys: total_patents, counts_by_status, pending_analysis,
        high_risk_count, recent_runs, last_scan_date.
    """
    total = db.get_patent_count()
    counts = db.get_patent_count_by_status()
    pending = len(db.get_patents_without_ai_analysis())
    recent_runs = db.get_recent_search_runs(limit=5)
    last_scan = db.get_last_run_date("api")

    # Count high-risk patents (those with AI score >= 70)
    all_patents = db.get_all_patents(limit=1000)
    high_risk = 0
    for patent in all_patents:
        analysis_json = db.get_ai_analysis(patent.patent_number)
        if analysis_json:
            try:
                data = json.loads(analysis_json)
                if data.get("risk_level") == "high":
                    high_risk += 1
            except (json.JSONDecodeError, KeyError):
                pass

    return {
        "total_patents": total,
        "counts_by_status": counts,
        "pending_analysis": pending,
        "high_risk_count": high_risk,
        "recent_runs": recent_runs,
        "last_scan_date": last_scan,
    }


# --- Internal helpers ---


def _search_api(
    config, db, matcher,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[list[Alert], int, str | None]:
    """Search USPTO API. Returns (alerts, total_fetched, error_or_none)."""
    alerts = []
    start_time = time.time()
    error = None

    client = USPTOClient(
        api_key=config.api.api_key,
        rate_limit=config.api.rate_limit_per_minute,
        timeout=config.api.timeout_seconds,
        max_retries=config.api.max_retries,
    )

    # Use provided dates, or auto-detect from last run
    if date_from is None:
        last_run = db.get_last_run_date("api")
        date_from = last_run if last_run else date.today() - timedelta(days=config.initial_lookback_days)
    if date_to is None:
        date_to = date.today()

    logger.info(f"API search: {date_from} to {date_to}")

    run = SearchRun(run_at=datetime.now(), source="api")
    fetched_count = 0

    # Collect keywords from all search criteria to narrow the API query
    all_keywords = []
    for criteria in config.search_criteria:
        all_keywords.extend(criteria.keywords)

    try:
        patents = client.search_design_patents(
            date_from, date_to,
            keywords=all_keywords if all_keywords else None,
        )
        run.results_count = len(patents)
        fetched_count = len(patents)

        for patent in patents:
            if db.patent_exists(patent.patent_number):
                continue

            matched = matcher.match(patent)
            if matched:
                db.insert_patent(patent, matched)
                alert = Alert(patent=patent, matched_criteria=matched)
                alerts.append(alert)

        run.new_matches_count = len(alerts)

    except Exception as e:
        logger.error(f"API search failed: {e}")
        run.error = str(e)
        error = f"API search failed: {e}"

    run.duration_seconds = time.time() - start_time
    run.query_params = json.dumps({
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    })
    db.log_search_run(run)

    return alerts, fetched_count, error


def _search_gazette(db, matcher) -> tuple[list[Alert], int, str | None]:
    """Search Official Gazette. Returns (alerts, total_fetched, error_or_none)."""
    alerts = []
    start_time = time.time()
    error = None

    scraper = GazetteScraper()
    run = SearchRun(run_at=datetime.now(), source="gazette")
    fetched_count = 0

    try:
        patents = scraper.scrape_current_week()
        run.results_count = len(patents)
        fetched_count = len(patents)

        for patent in patents:
            if db.patent_exists(patent.patent_number):
                continue

            matched = matcher.match(patent)
            if matched:
                db.insert_patent(patent, matched)
                alert = Alert(patent=patent, matched_criteria=matched)
                alerts.append(alert)

        run.new_matches_count = len(alerts)

    except Exception as e:
        logger.error(f"Gazette scrape failed: {e}")
        run.error = str(e)
        error = f"Gazette scrape failed: {e}"

    run.duration_seconds = time.time() - start_time
    db.log_search_run(run)

    return alerts, fetched_count, error


def _analyze_single_patent(
    patent: Patent,
    analyzer,
    image_fetcher,
    product_images: list[tuple[str, bytes]],
    db: Database,
) -> AnalysisResult | None:
    """Run AI analysis on a single patent and store the result."""
    patent_image = image_fetcher.fetch_patent_image(patent)
    analysis = analyzer.analyze(patent, patent_image, product_images)

    # Store in database
    db.update_ai_analysis(patent.patent_number, json.dumps({
        "similarity_score": analysis.similarity_score,
        "risk_level": analysis.risk_level,
        "recommendation": analysis.recommendation,
        "reasoning": analysis.reasoning,
        "patent_image_used": analysis.patent_image_used,
        "product_images_used": analysis.product_images_used,
        "model_used": analysis.model_used,
        "analyzed_at": analysis.analyzed_at.isoformat(),
        "error": analysis.error,
    }))

    return analysis
