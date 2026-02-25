"""Email notification system for patent alerts."""

import logging
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from jinja2 import Template

from .config import SmtpConfig
from .models import Alert, Patent

logger = logging.getLogger(__name__)

ALERT_TEMPLATE = Template("""
<!DOCTYPE html>
<html>
<head>
<style>
  body { font-family: Arial, sans-serif; color: #333; max-width: 700px; margin: 0 auto; }
  .header { background: #1a365d; color: white; padding: 20px; border-radius: 8px 8px 0 0; }
  .content { padding: 20px; border: 1px solid #e2e8f0; border-top: none; border-radius: 0 0 8px 8px; }
  .patent-card { background: #f7fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin: 12px 0; }
  .urgency-high { border-left: 4px solid #e53e3e; }
  .urgency-medium { border-left: 4px solid #dd6b20; }
  .urgency-low { border-left: 4px solid #38a169; }
  .urgency-expired { border-left: 4px solid #718096; }
  .label { font-weight: bold; color: #4a5568; font-size: 13px; }
  .value { color: #1a202c; margin-bottom: 8px; }
  .deadline { font-weight: bold; font-size: 15px; }
  .deadline-urgent { color: #e53e3e; }
  .deadline-ok { color: #38a169; }
  .match-tag { display: inline-block; background: #ebf8ff; color: #2b6cb0; padding: 2px 8px; border-radius: 4px; margin: 2px; font-size: 12px; }
  a { color: #2b6cb0; }
  .footer { font-size: 12px; color: #a0aec0; margin-top: 20px; padding-top: 12px; border-top: 1px solid #e2e8f0; }
</style>
</head>
<body>
<div class="header">
  <h2 style="margin:0;">{{ title }}</h2>
  <p style="margin:4px 0 0 0; opacity:0.9;">{{ subtitle }}</p>
</div>
<div class="content">
{% for alert in alerts %}
  <div class="patent-card urgency-{{ alert.patent.urgency }}">
    <div class="label">Patent Number</div>
    <div class="value"><a href="{{ alert.patent.uspto_url }}">{{ alert.patent.patent_number }}</a></div>

    <div class="label">Title</div>
    <div class="value">{{ alert.patent.title }}</div>

    <div class="label">Issue Date</div>
    <div class="value">{{ alert.patent.issue_date.strftime('%B %d, %Y') }}</div>

    <div class="label">PGR Deadline</div>
    <div class="value deadline {% if alert.patent.pgr_months_remaining < 3 %}deadline-urgent{% else %}deadline-ok{% endif %}">
      {{ alert.patent.pgr_deadline.strftime('%B %d, %Y') }}
      ({{ "%.1f"|format(alert.patent.pgr_months_remaining) }} months remaining)
    </div>

    {% if alert.patent.assignee %}
    <div class="label">Assignee</div>
    <div class="value">{{ alert.patent.assignee }}</div>
    {% endif %}

    {% if alert.patent.inventors %}
    <div class="label">Inventors</div>
    <div class="value">{{ alert.patent.inventors | join(', ') }}</div>
    {% endif %}

    {% if alert.patent.classification_us %}
    <div class="label">US Classification</div>
    <div class="value">{{ alert.patent.classification_us }}</div>
    {% endif %}

    {% if alert.patent.classification_cpc %}
    <div class="label">CPC Classification</div>
    <div class="value">{{ alert.patent.classification_cpc }}</div>
    {% endif %}

    {% if alert.matched_criteria %}
    <div class="label">Matched Criteria</div>
    <div class="value">
      {% for criteria in alert.matched_criteria %}
        <span class="match-tag">{{ criteria }}</span>
      {% endfor %}
    </div>
    {% endif %}

    {% if alert.ai_analysis %}
    <div style="margin-top: 12px; padding: 12px; background: #fff; border: 1px solid #e2e8f0; border-radius: 6px;">
      <div class="label" style="margin-bottom: 8px;">AI Design Analysis</div>
      <div style="display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px;">
        <span style="font-size: 28px; font-weight: bold;
          {% if alert.ai_analysis.similarity_score >= 70 %}color: #e53e3e;
          {% elif alert.ai_analysis.similarity_score >= 40 %}color: #dd6b20;
          {% else %}color: #38a169;{% endif %}">
          {{ alert.ai_analysis.similarity_score }}%
        </span>
        <span style="font-size: 13px; color: #718096;">similarity to your products</span>
      </div>
      <div style="margin-bottom: 6px;">
        <span class="label">Risk: </span>
        <span style="font-weight: bold;
          {% if alert.ai_analysis.risk_level == 'high' %}color: #e53e3e;
          {% elif alert.ai_analysis.risk_level == 'medium' %}color: #dd6b20;
          {% elif alert.ai_analysis.risk_level == 'low' %}color: #38a169;
          {% else %}color: #718096;{% endif %}">
          {{ alert.ai_analysis.risk_level | upper }}
        </span>
        &nbsp;&middot;&nbsp;
        <span style="display: inline-block; padding: 2px 10px; border-radius: 4px; font-size: 12px; font-weight: bold;
          {% if alert.ai_analysis.recommendation == 'flag' %}background: #fed7d7; color: #9b2c2c;
          {% elif alert.ai_analysis.recommendation == 'monitor' %}background: #fefcbf; color: #975a16;
          {% else %}background: #c6f6d5; color: #276749;{% endif %}">
          {{ alert.ai_analysis.recommendation | upper }}
        </span>
      </div>
      <div style="margin-top: 8px; font-size: 13px; color: #4a5568; line-height: 1.5;">
        {{ alert.ai_analysis.reasoning }}
      </div>
      <div style="margin-top: 6px; font-size: 11px; color: #a0aec0;">
        {% if alert.ai_analysis.patent_image_used %}Visual + text analysis{% else %}Text-only analysis (no patent image available){% endif %}
        &middot; {{ alert.ai_analysis.product_images_used | length }} product image(s) compared
      </div>
    </div>
    {% endif %}
  </div>
{% endfor %}

  <div class="footer">
    <p>Patent Monitor — automated design patent tracking</p>
    <p>To manage your search criteria, edit config.yaml</p>
  </div>
</div>
</body>
</html>
""")


class EmailNotifier:
    """Sends patent alert emails via SMTP."""

    def __init__(self, config: SmtpConfig):
        self.config = config

    def send_new_patent_alerts(self, alerts: list[Alert]) -> bool:
        """Send email alerts for newly found patents.

        Args:
            alerts: List of Alert objects to send.

        Returns:
            True if emails sent successfully.
        """
        if not alerts:
            return True

        count = len(alerts)
        subject = f"Patent Monitor: {count} new design patent{'s' if count > 1 else ''} found"

        html = ALERT_TEMPLATE.render(
            title=f"{count} New Design Patent{'s' if count > 1 else ''} Found",
            subtitle=f"Detected on {date.today().strftime('%B %d, %Y')}",
            alerts=alerts,
        )

        return self._send_email(subject, html)

    def send_pgr_reminder(self, patents: list[Patent], months_threshold: float) -> bool:
        """Send PGR deadline reminder for flagged patents.

        Args:
            patents: Patents approaching PGR deadline.
            months_threshold: The reminder threshold that triggered this.

        Returns:
            True if email sent successfully.
        """
        if not patents:
            return True

        alerts = [Alert(patent=p) for p in patents]
        count = len(patents)
        subject = f"PGR DEADLINE REMINDER: {count} patent{'s' if count > 1 else ''} — less than {months_threshold} months remaining"

        html = ALERT_TEMPLATE.render(
            title=f"PGR Deadline Approaching",
            subtitle=f"{count} flagged patent{'s' if count > 1 else ''} with less than {months_threshold} months until Post-Grant Review deadline",
            alerts=alerts,
        )

        return self._send_email(subject, html)

    def send_test_email(self) -> bool:
        """Send a test email to verify configuration."""
        subject = "Patent Monitor — Test Email"
        html = """
        <html><body>
        <h2>Patent Monitor Test</h2>
        <p>If you received this email, your notification settings are configured correctly.</p>
        <p>The monitor will send alerts when new design patents matching your criteria are found.</p>
        </body></html>
        """
        return self._send_email(subject, html)

    def _send_email(self, subject: str, html_body: str) -> bool:
        """Send an HTML email to all configured recipients."""
        if not self.config.enabled:
            logger.info("Email notifications disabled in config")
            return True

        if not self.config.recipients:
            logger.warning("No email recipients configured")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.config.user
            msg["To"] = ", ".join(self.config.recipients)
            msg.attach(MIMEText(html_body, "html"))

            if self.config.use_tls:
                server = smtplib.SMTP(self.config.host, self.config.port)
                server.starttls()
            else:
                server = smtplib.SMTP(self.config.host, self.config.port)

            server.login(self.config.user, self.config.password)
            server.sendmail(self.config.user, self.config.recipients, msg.as_string())
            server.quit()

            logger.info(f"Email sent: '{subject}' to {len(self.config.recipients)} recipients")
            return True

        except smtplib.SMTPException as e:
            logger.error(f"Failed to send email: {e}")
            return False
