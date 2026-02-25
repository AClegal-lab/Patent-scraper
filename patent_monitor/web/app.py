"""Flask application factory."""

import os
from pathlib import Path

from flask import Flask

from ..config import load_config
from ..db import Database
from .tasks import TaskManager


def create_app(config_path: str | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        config_path: Path to patent_monitor config.yaml.
                     Defaults to CONFIG_PATH env var or 'config.yaml'.
    """
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )

    # Load patent monitor config
    if config_path is None:
        config_path = os.environ.get("CONFIG_PATH", "config.yaml")

    pm_config = load_config(config_path)
    app.config["PM_CONFIG"] = pm_config
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "patent-monitor-dev-key")

    # Initialize database
    db = Database(pm_config.database_path)
    db.init_db()
    app.config["DB"] = db

    # Initialize task manager
    task_manager = TaskManager()
    app.config["TASK_MANAGER"] = task_manager

    # Register routes
    from .routes import bp
    app.register_blueprint(bp)

    # Register password authentication (enabled when APP_PASSWORD is set)
    from .auth import init_auth
    init_auth(app)

    # Teardown: close DB when app shuts down
    @app.teardown_appcontext
    def close_db(exception):
        pass  # DB stays open for the app lifetime

    return app
