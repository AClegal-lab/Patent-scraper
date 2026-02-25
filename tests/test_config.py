"""Tests for configuration loading."""

import os
from pathlib import Path

import pytest

from patent_monitor.config import load_config, validate_config


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_load_config():
    config = load_config(str(FIXTURES_DIR / "sample_config.yaml"))

    assert config.api.base_url == "https://api.uspto.gov"
    assert config.api.rate_limit_per_minute == 50
    assert len(config.search_criteria) == 1
    assert config.search_criteria[0].name == "Eyewear Design Patents"
    assert "D16/300" in config.search_criteria[0].us_classes
    assert config.initial_lookback_days == 30
    assert config.notifications.email.enabled is False
    assert config.database_path == ":memory:"


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("nonexistent.yaml")


def test_validate_config_missing_api_key():
    config = load_config(str(FIXTURES_DIR / "sample_config.yaml"))
    config.api.api_key = ""
    errors = validate_config(config)
    assert any("USPTO_API_KEY" in e for e in errors)


def test_validate_config_no_criteria():
    config = load_config(str(FIXTURES_DIR / "sample_config.yaml"))
    config.api.api_key = "test-key"
    config.search_criteria = []
    errors = validate_config(config)
    assert any("search criteria" in e.lower() for e in errors)


def test_validate_config_email_disabled_no_errors():
    config = load_config(str(FIXTURES_DIR / "sample_config.yaml"))
    config.api.api_key = "test-key"
    config.notifications.email.enabled = False
    errors = validate_config(config)
    assert len(errors) == 0


def test_validate_config_email_enabled_missing_creds():
    config = load_config(str(FIXTURES_DIR / "sample_config.yaml"))
    config.api.api_key = "test-key"
    config.notifications.email.enabled = True
    config.notifications.email.user = ""
    config.notifications.email.password = ""
    errors = validate_config(config)
    assert any("SMTP_USER" in e for e in errors)


def test_search_criteria_loaded():
    config = load_config(str(FIXTURES_DIR / "sample_config.yaml"))
    criteria = config.search_criteria[0]
    assert "D16/300" in criteria.us_classes
    assert "G02C" in criteria.cpc_classes
    assert "eyeglasses" in criteria.keywords
    assert "Our Company" in criteria.assignee_exclude
