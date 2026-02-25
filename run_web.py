"""Entry point for the Patent Monitor web UI."""

import os
import sys

from patent_monitor.web.app import create_app

app = create_app(config_path=os.environ.get("CONFIG_PATH", "config.yaml"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8888))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    print(f"Starting Patent Monitor Web UI at http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
