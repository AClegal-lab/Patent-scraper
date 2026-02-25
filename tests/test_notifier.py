"""Tests for email notifier."""

from datetime import date
from unittest.mock import MagicMock, patch

from patent_monitor.config import SmtpConfig
from patent_monitor.models import Alert, Patent
from patent_monitor.notifier import EmailNotifier


def make_smtp_config(**kwargs) -> SmtpConfig:
    defaults = {
        "enabled": True,
        "host": "smtp.test.com",
        "port": 587,
        "use_tls": True,
        "user": "test@test.com",
        "password": "secret",
        "recipients": ["user1@test.com", "user2@test.com"],
    }
    defaults.update(kwargs)
    return SmtpConfig(**defaults)


def make_alert(**kwargs) -> Alert:
    patent = Patent(
        patent_number="D1012345",
        title="Eyeglasses Frame",
        issue_date=date(2026, 2, 18),
        assignee="Acme Eyewear Inc.",
        inventors=["Smith, John"],
        classification_us="D16/300",
        classification_cpc="G02C 1/00",
    )
    return Alert(
        patent=patent,
        matched_criteria=["US class: D16/300"],
        **kwargs,
    )


@patch("patent_monitor.notifier.smtplib.SMTP")
def test_send_new_patent_alerts(mock_smtp_cls):
    mock_server = MagicMock()
    mock_smtp_cls.return_value = mock_server

    config = make_smtp_config()
    notifier = EmailNotifier(config)
    alerts = [make_alert()]

    result = notifier.send_new_patent_alerts(alerts)

    assert result is True
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("test@test.com", "secret")
    mock_server.sendmail.assert_called_once()
    mock_server.quit.assert_called_once()

    # Check email was sent to correct recipients
    call_args = mock_server.sendmail.call_args
    assert call_args[0][0] == "test@test.com"
    assert call_args[0][1] == ["user1@test.com", "user2@test.com"]


@patch("patent_monitor.notifier.smtplib.SMTP")
def test_send_empty_alerts(mock_smtp_cls):
    config = make_smtp_config()
    notifier = EmailNotifier(config)

    result = notifier.send_new_patent_alerts([])
    assert result is True
    mock_smtp_cls.assert_not_called()


def test_send_disabled():
    config = make_smtp_config(enabled=False)
    notifier = EmailNotifier(config)

    result = notifier.send_new_patent_alerts([make_alert()])
    assert result is True


def test_send_no_recipients():
    config = make_smtp_config(recipients=[])
    notifier = EmailNotifier(config)

    result = notifier.send_new_patent_alerts([make_alert()])
    assert result is False


@patch("patent_monitor.notifier.smtplib.SMTP")
def test_send_test_email(mock_smtp_cls):
    mock_server = MagicMock()
    mock_smtp_cls.return_value = mock_server

    config = make_smtp_config()
    notifier = EmailNotifier(config)

    result = notifier.send_test_email()
    assert result is True
    mock_server.sendmail.assert_called_once()


@patch("patent_monitor.notifier.smtplib.SMTP")
def test_send_pgr_reminder(mock_smtp_cls):
    mock_server = MagicMock()
    mock_smtp_cls.return_value = mock_server

    config = make_smtp_config()
    notifier = EmailNotifier(config)

    patent = Patent(
        patent_number="D1012345",
        title="Eyeglasses Frame",
        issue_date=date(2026, 2, 18),
    )

    result = notifier.send_pgr_reminder([patent], 8.0)
    assert result is True
    mock_server.sendmail.assert_called_once()

    # Check subject mentions PGR deadline
    call_args = mock_server.sendmail.call_args
    email_content = call_args[0][2]
    assert "PGR" in email_content
