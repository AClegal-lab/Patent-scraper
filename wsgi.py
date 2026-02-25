"""WSGI entry point for production deployment (gunicorn/uWSGI)."""

from run_web import app  # noqa: F401

# Usage: gunicorn wsgi:app -b 0.0.0.0:8080 -w 2
