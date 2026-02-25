"""Configuration loading from YAML and environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class ApiConfig:
    base_url: str = "https://api.uspto.gov"
    rate_limit_per_minute: int = 50
    timeout_seconds: int = 30
    max_retries: int = 3
    api_key: str = ""


@dataclass
class SmtpConfig:
    enabled: bool = True
    host: str = "smtp.gmail.com"
    port: int = 587
    use_tls: bool = True
    user: str = ""
    password: str = ""
    recipients: list[str] = field(default_factory=list)


@dataclass
class NotificationConfig:
    email: SmtpConfig = field(default_factory=SmtpConfig)
    pgr_reminder_months: list[float] = field(default_factory=lambda: [6, 8, 8.5])


@dataclass
class SearchCriteriaConfig:
    name: str = ""
    us_classes: list[str] = field(default_factory=list)
    cpc_classes: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    assignee_exclude: list[str] = field(default_factory=list)


@dataclass
class SourcesConfig:
    uspto_api: bool = True
    official_gazette: bool = True


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/patent-monitor.log"
    max_size_mb: int = 10
    backup_count: int = 5


@dataclass
class AiConfig:
    enabled: bool = False
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    rate_limit_per_minute: int = 10
    max_tokens: int = 1024
    timeout_seconds: int = 60
    product_images_dir: str = "data/product_images"
    similarity_threshold: int = 30   # auto-dismiss below this score
    max_product_images: int = 3


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class Config:
    api: ApiConfig = field(default_factory=ApiConfig)
    search_criteria: list[SearchCriteriaConfig] = field(default_factory=list)
    initial_lookback_days: int = 90
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    database_path: str = "data/patents.db"
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    ai: AiConfig = field(default_factory=AiConfig)
    web: WebConfig = field(default_factory=WebConfig)


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> Config:
    """Load configuration from YAML file and environment variables."""
    # Load .env file for secrets
    env_file = Path(env_path)
    if env_file.exists():
        load_dotenv(env_file)

    # Load YAML config
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_file) as f:
        raw = yaml.safe_load(f) or {}

    config = Config()

    # API settings
    api_raw = raw.get("api", {})
    config.api = ApiConfig(
        base_url=api_raw.get("base_url", config.api.base_url),
        rate_limit_per_minute=api_raw.get("rate_limit_per_minute", config.api.rate_limit_per_minute),
        timeout_seconds=api_raw.get("timeout_seconds", config.api.timeout_seconds),
        max_retries=api_raw.get("max_retries", config.api.max_retries),
        api_key=os.environ.get("USPTO_API_KEY", ""),
    )

    # Search criteria
    for criteria_raw in raw.get("search_criteria", []):
        config.search_criteria.append(SearchCriteriaConfig(
            name=criteria_raw.get("name", ""),
            us_classes=criteria_raw.get("us_classes", []),
            cpc_classes=criteria_raw.get("cpc_classes", []),
            keywords=criteria_raw.get("keywords", []),
            assignee_exclude=criteria_raw.get("assignee_exclude", []),
        ))

    config.initial_lookback_days = raw.get("initial_lookback_days", config.initial_lookback_days)

    # Notification settings
    notif_raw = raw.get("notifications", {})
    email_raw = notif_raw.get("email", {})
    config.notifications = NotificationConfig(
        email=SmtpConfig(
            enabled=email_raw.get("enabled", True),
            host=email_raw.get("smtp_host", "smtp.gmail.com"),
            port=email_raw.get("smtp_port", 587),
            use_tls=email_raw.get("use_tls", True),
            user=os.environ.get("SMTP_USER", ""),
            password=os.environ.get("SMTP_PASSWORD", ""),
            recipients=email_raw.get("recipients", []),
        ),
        pgr_reminder_months=notif_raw.get("pgr_reminder_months", [6, 8, 8.5]),
    )

    # Sources
    sources_raw = raw.get("sources", {})
    config.sources = SourcesConfig(
        uspto_api=sources_raw.get("uspto_api", True),
        official_gazette=sources_raw.get("official_gazette", True),
    )

    # Database
    db_raw = raw.get("database", {})
    config.database_path = db_raw.get("path", config.database_path)

    # Logging
    log_raw = raw.get("logging", {})
    config.logging = LoggingConfig(
        level=log_raw.get("level", "INFO"),
        file=log_raw.get("file", "logs/patent-monitor.log"),
        max_size_mb=log_raw.get("max_size_mb", 10),
        backup_count=log_raw.get("backup_count", 5),
    )

    # Web UI settings
    web_raw = raw.get("web", {})
    config.web = WebConfig(
        host=web_raw.get("host", "127.0.0.1"),
        port=web_raw.get("port", 5000),
    )

    # AI analysis settings
    ai_raw = raw.get("ai", {})
    config.ai = AiConfig(
        enabled=ai_raw.get("enabled", False),
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        model=ai_raw.get("model", "claude-sonnet-4-20250514"),
        rate_limit_per_minute=ai_raw.get("rate_limit_per_minute", 10),
        max_tokens=ai_raw.get("max_tokens", 1024),
        timeout_seconds=ai_raw.get("timeout_seconds", 60),
        product_images_dir=ai_raw.get("product_images_dir", "data/product_images"),
        similarity_threshold=ai_raw.get("similarity_threshold", 30),
        max_product_images=ai_raw.get("max_product_images", 3),
    )

    return config


def validate_config(config: Config) -> list[str]:
    """Validate configuration and return a list of errors (empty if valid)."""
    errors = []

    if not config.api.api_key:
        errors.append("USPTO_API_KEY environment variable is not set. Register at https://data.uspto.gov/myodp")

    if not config.search_criteria:
        errors.append("No search criteria defined in config.yaml")

    if config.notifications.email.enabled:
        if not config.notifications.email.user:
            errors.append("SMTP_USER environment variable is not set")
        if not config.notifications.email.password:
            errors.append("SMTP_PASSWORD environment variable is not set")
        if not config.notifications.email.recipients:
            errors.append("No email recipients defined in config.yaml")

    for i, criteria in enumerate(config.search_criteria):
        if not criteria.us_classes and not criteria.cpc_classes and not criteria.keywords:
            errors.append(f"Search criteria [{i}] '{criteria.name}' has no classes or keywords defined")

    if config.ai.enabled:
        if not config.ai.api_key:
            errors.append("ANTHROPIC_API_KEY environment variable is not set (required when ai.enabled is true)")
        product_dir = Path(config.ai.product_images_dir)
        if not product_dir.exists():
            errors.append(f"Product images directory not found: {config.ai.product_images_dir}")
        else:
            image_files = (
                list(product_dir.glob("*.png"))
                + list(product_dir.glob("*.jpg"))
                + list(product_dir.glob("*.jpeg"))
                + list(product_dir.glob("*.webp"))
            )
            if not image_files:
                errors.append(f"No image files found in product images directory: {config.ai.product_images_dir}")

    return errors
