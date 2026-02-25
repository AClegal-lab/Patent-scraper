"""Simple shared-password authentication for the Patent Monitor web UI."""

import os

from flask import Flask, redirect, render_template, request, session


def init_auth(app: Flask):
    """Register authentication middleware on the Flask app.

    Set the APP_PASSWORD environment variable to enable password protection.
    If APP_PASSWORD is not set, authentication is disabled (open access).
    """
    password = os.environ.get("APP_PASSWORD", "")

    if not password:
        return  # No password configured — skip auth entirely

    @app.before_request
    def require_login():
        # Allow static files and the login/logout routes without auth
        if request.endpoint in ("login", "logout", "main.static", "static"):
            return
        if request.path in ("/login", "/logout"):
            return
        if not session.get("authenticated"):
            return redirect("/login")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            if request.form.get("password") == password:
                session["authenticated"] = True
                return redirect("/")
            error = "Incorrect password"
        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect("/login")
