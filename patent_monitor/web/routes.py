"""Flask routes for the patent monitor web UI."""

import json
import logging
from datetime import date, datetime

from flask import Blueprint, Response, current_app, jsonify, redirect, render_template, request, send_file, url_for

from ..service import get_dashboard_stats, run_ai_analysis, run_scan

logger = logging.getLogger(__name__)

from pathlib import Path

bp = Blueprint(
    "main",
    __name__,
    static_folder=str(Path(__file__).parent / "static"),
    static_url_path="/static",
)


def _get_db():
    return current_app.config["DB"]


def _get_config():
    return current_app.config["PM_CONFIG"]


def _get_task_manager():
    return current_app.config["TASK_MANAGER"]


# --- Page Routes ---


@bp.route("/")
def dashboard():
    """Dashboard with stats, action buttons, and recent patents."""
    db = _get_db()
    config = _get_config()
    tm = _get_task_manager()

    stats = get_dashboard_stats(db)

    from datetime import timedelta
    today = date.today()
    today_minus_90 = today - timedelta(days=90)

    # If date filters are present, show only patents in that range
    date_from_str = request.args.get("date_from", "")
    date_to_str = request.args.get("date_to", "")
    date_from_val = None
    date_to_val = None

    if date_from_str and date_to_str:
        try:
            date_from_val = date.fromisoformat(date_from_str)
            date_to_val = date.fromisoformat(date_to_str)
            recent_patents = db.get_patents_by_date_range(date_from_val, date_to_val)
        except ValueError:
            recent_patents = db.get_all_patents(limit=10)
    else:
        recent_patents = db.get_all_patents(limit=10)

    # Attach AI analysis data to recent patents for display
    patents_with_analysis = []
    for patent in recent_patents:
        analysis_json = db.get_ai_analysis(patent.patent_number)
        analysis = None
        if analysis_json:
            try:
                analysis = json.loads(analysis_json)
            except json.JSONDecodeError:
                pass
        patents_with_analysis.append((patent, analysis))

    return render_template(
        "dashboard.html",
        stats=stats,
        patents_with_analysis=patents_with_analysis,
        ai_enabled=config.ai.enabled,
        has_running_task=tm.has_running_task(),
        today=today.isoformat(),
        today_minus_90=today_minus_90.isoformat(),
        date_from=date_from_str or today_minus_90.isoformat(),
        date_to=date_to_str or today.isoformat(),
    )


@bp.route("/patents")
def patent_list():
    """Patent list with filtering and pagination."""
    db = _get_db()

    status_filter = request.args.get("status", "")
    risk_filter = request.args.get("risk", "")
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page

    if status_filter:
        patents = db.get_patents_by_status(status_filter)
    else:
        patents = db.get_all_patents(limit=per_page, offset=offset)

    total = db.get_patent_count()
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Attach AI analysis and apply risk filter
    patents_with_analysis = []
    for patent in patents:
        analysis_json = db.get_ai_analysis(patent.patent_number)
        analysis = None
        if analysis_json:
            try:
                analysis = json.loads(analysis_json)
            except json.JSONDecodeError:
                pass

        if risk_filter:
            if analysis and analysis.get("risk_level") == risk_filter:
                patents_with_analysis.append((patent, analysis))
            elif not risk_filter:
                patents_with_analysis.append((patent, analysis))
        else:
            patents_with_analysis.append((patent, analysis))

    return render_template(
        "patents.html",
        patents_with_analysis=patents_with_analysis,
        status_filter=status_filter,
        risk_filter=risk_filter,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@bp.route("/patents/<patent_number>")
def patent_detail(patent_number):
    """Patent detail page with full metadata and AI analysis."""
    db = _get_db()
    config = _get_config()

    patent = db.get_patent(patent_number)
    if not patent:
        return render_template("404.html", message=f"Patent {patent_number} not found"), 404

    analysis = None
    analysis_json = db.get_ai_analysis(patent_number)
    if analysis_json:
        try:
            analysis = json.loads(analysis_json)
        except json.JSONDecodeError:
            pass

    # Get matched criteria from DB
    cur = db.conn.execute(
        "SELECT matched_criteria FROM patents WHERE patent_number = ?",
        (patent_number,),
    )
    row = cur.fetchone()
    matched_criteria = []
    if row and row["matched_criteria"]:
        try:
            matched_criteria = json.loads(row["matched_criteria"])
        except json.JSONDecodeError:
            pass

    return render_template(
        "patent_detail.html",
        patent=patent,
        analysis=analysis,
        matched_criteria=matched_criteria,
        ai_enabled=config.ai.enabled,
    )


# --- API Routes ---


@bp.route("/api/scan", methods=["POST"])
def api_scan():
    """Start a background scan task."""
    tm = _get_task_manager()

    if tm.has_running_task("scan"):
        return jsonify({"error": "A scan is already running"}), 409

    config = _get_config()
    db = _get_db()

    # Parse optional date range from request body
    date_from = None
    date_to = None
    data = request.get_json(silent=True) or {}
    if data.get("date_from"):
        try:
            date_from = date.fromisoformat(data["date_from"])
        except ValueError:
            return jsonify({"error": "Invalid date_from format. Use YYYY-MM-DD"}), 400
    if data.get("date_to"):
        try:
            date_to = date.fromisoformat(data["date_to"])
        except ValueError:
            return jsonify({"error": "Invalid date_to format. Use YYYY-MM-DD"}), 400

    task_id = tm.start_task(
        "scan",
        _run_scan_task,
        config,
        db,
        date_from,
        date_to,
    )

    return jsonify({"task_id": task_id})


@bp.route("/api/analyze", methods=["POST"])
def api_analyze_all():
    """Start background AI analysis on all unanalyzed patents."""
    tm = _get_task_manager()
    config = _get_config()

    if not config.ai.enabled:
        return jsonify({"error": "AI analysis is not enabled in configuration"}), 400

    if tm.has_running_task("analyze"):
        return jsonify({"error": "An analysis is already running"}), 409

    db = _get_db()

    task_id = tm.start_task(
        "analyze",
        _run_analyze_task,
        config,
        db,
    )

    return jsonify({"task_id": task_id})


@bp.route("/api/analyze/<patent_number>", methods=["POST"])
def api_analyze_single(patent_number):
    """Start background AI analysis on a single patent."""
    tm = _get_task_manager()
    config = _get_config()

    if not config.ai.enabled:
        return jsonify({"error": "AI analysis is not enabled in configuration"}), 400

    db = _get_db()
    patent = db.get_patent(patent_number)
    if not patent:
        return jsonify({"error": f"Patent {patent_number} not found"}), 404

    task_id = tm.start_task(
        "analyze",
        _run_analyze_task,
        config,
        db,
        [patent_number],
    )

    return jsonify({"task_id": task_id})


@bp.route("/api/tasks/<task_id>")
def api_task_status(task_id):
    """Get task status for polling."""
    tm = _get_task_manager()
    task = tm.get_task(task_id)

    if not task:
        return jsonify({"error": "Task not found"}), 404

    response = {
        "task_id": task.id,
        "name": task.name,
        "status": task.status,
        "message": task.message,
    }

    if task.status == "completed" and task.result:
        # Serialize result
        result = task.result
        if hasattr(result, "__dataclass_fields__"):
            response["result"] = _serialize_result(result)
        else:
            response["result"] = str(result)

    if task.error:
        response["error"] = task.error

    return jsonify(response)


@bp.route("/api/patents/<patent_number>/status", methods=["POST"])
def api_update_status(patent_number):
    """Update a patent's status."""
    db = _get_db()
    data = request.get_json()

    if not data or "status" not in data:
        return jsonify({"error": "Missing 'status' field"}), 400

    new_status = data["status"]
    valid_statuses = ["new", "reviewed", "flagged", "dismissed"]
    if new_status not in valid_statuses:
        return jsonify({"error": f"Invalid status. Must be one of: {valid_statuses}"}), 400

    patent = db.get_patent(patent_number)
    if not patent:
        return jsonify({"error": f"Patent {patent_number} not found"}), 404

    db.update_patent_status(patent_number, new_status)

    return jsonify({"success": True, "patent_number": patent_number, "status": new_status})


@bp.route("/api/patents/<patent_number>/image")
def api_patent_image(patent_number):
    """Serve patent design image, fetching and caching on first request."""
    import io

    from ..image_fetcher import PatentImageFetcher

    db = _get_db()
    config = _get_config()

    patent = db.get_patent(patent_number)
    if not patent:
        return jsonify({"error": "Patent not found"}), 404

    # Check disk cache first
    cache_dir = Path(config.database_path).resolve().parent / "patent_image_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{patent_number}.png"

    if cache_file.exists():
        return send_file(str(cache_file), mimetype="image/png")

    # Fetch the image
    fetcher = PatentImageFetcher(timeout=config.api.timeout_seconds)
    image_bytes = fetcher.fetch_patent_image(patent)

    if not image_bytes:
        # Return a 1x1 transparent PNG as placeholder
        return Response(status=204)

    # Cache to disk
    cache_file.write_bytes(image_bytes)

    return send_file(io.BytesIO(image_bytes), mimetype="image/png")


# --- Task wrapper functions ---


def _run_scan_task(config, db, date_from=None, date_to=None, progress_callback=None):
    """Wrapper for run_scan that returns a serializable result."""
    result = run_scan(
        config, db,
        date_from=date_from,
        date_to=date_to,
        progress_callback=progress_callback,
    )
    return {
        "new_matches": result.new_matches,
        "total_fetched": result.total_fetched,
        "errors": result.errors,
        "duration": f"{result.duration_seconds:.1f}s",
    }


def _run_analyze_task(config, db, patent_numbers=None, progress_callback=None):
    """Wrapper for run_ai_analysis that returns a serializable result."""
    result = run_ai_analysis(
        config, db,
        patent_numbers=patent_numbers,
        progress_callback=progress_callback,
    )
    return {
        "analyzed_count": len(result.analyzed),
        "skipped": result.skipped,
        "errors": result.errors,
        "duration": f"{result.duration_seconds:.1f}s",
    }


def _serialize_result(obj):
    """Best-effort serialize a dataclass-like object."""
    if isinstance(obj, dict):
        return obj
    return str(obj)
