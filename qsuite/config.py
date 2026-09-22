"""Configuration: YAML file + environment overrides.

Credentials never belong in the YAML you commit. Every credential field reads
from the environment first (``QSUITE_<NAME>``), and the example config ships
with placeholders pointing at env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

from .models import Cabin, Route

DEFAULT_CONFIG_PATHS = ["qsuite.yml", "qsuite.yaml", "config/qsuite.yml"]

#: Config keys read from the environment when absent from the file.
ENV_CREDENTIALS = {
    "ba_username": "QSUITE_BA_USERNAME",
    "ba_password": "QSUITE_BA_PASSWORD",
    "qantas_token": "QSUITE_QANTAS_TOKEN",
    "qatar_username": "QSUITE_QATAR_USERNAME",
    "qatar_password": "QSUITE_QATAR_PASSWORD",
}


@dataclass
class AlertConfig:
    console: bool = True
    webhook_url: Optional[str] = None
    webhook_format: str = "slack"          # slack | discord | raw
    email_to: Optional[str] = None
    email_from: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_starttls: bool = True
    #: Only alert on space we could confirm is Qsuite metal.
    qsuite_only: bool = False
    #: Only alert on dates with at least this many seats.
    min_seats: int = 1
    #: Ignore anything pricier than this, if the engine reported a price.
    max_miles: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AlertConfig":
        known = {f for f in cls.__dataclass_fields__}
        out = cls(**{k: v for k, v in (d or {}).items() if k in known})
        out.webhook_url = out.webhook_url or os.getenv("QSUITE_WEBHOOK_URL")
        out.smtp_password = out.smtp_password or os.getenv("QSUITE_SMTP_PASSWORD")
        return out


@dataclass
class Config:
    route: Route = field(default_factory=lambda: Route("YUL", "SIN"))
    cabin: Cabin = Cabin.BUSINESS
    days: int = 330
    start: Optional[str] = None
    end: Optional[str] = None
    providers: list[str] = field(default_factory=lambda: ["alaska", "ba", "qantas"])
    db_path: str = "data/qsuite.sqlite3"
    out_dir: str = "out"
    capture_dir: Optional[str] = None
    global_concurrency: int = 6
    dry_run: bool = False
    headless: bool = True
    proxy: Optional[dict[str, str]] = None
    credentials: dict[str, str] = field(default_factory=dict)
    provider_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    alerts: AlertConfig = field(default_factory=AlertConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        data: dict[str, Any] = {}
        chosen = _resolve_path(path)
        if chosen:
            if yaml is None:
                raise RuntimeError("PyYAML is required to read a config file")
            data = yaml.safe_load(chosen.read_text()) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        cfg = cls()
        scan = data.get("scan", {}) or {}
        if "route" in scan:
            cfg.route = Route.parse(scan["route"])
        if "cabin" in scan:
            cfg.cabin = Cabin(scan["cabin"])
        for key in ("days", "start", "end"):
            if key in scan:
                setattr(cfg, key, scan[key])
        if "providers" in scan:
            cfg.providers = list(scan["providers"])

        out = data.get("output", {}) or {}
        cfg.out_dir = out.get("dir", cfg.out_dir)

        storage = data.get("storage", {}) or {}
        cfg.db_path = storage.get("db_path", cfg.db_path)

        runtime = data.get("runtime", {}) or {}
        cfg.global_concurrency = int(runtime.get("global_concurrency", cfg.global_concurrency))
        cfg.headless = bool(runtime.get("headless", cfg.headless))
        cfg.capture_dir = runtime.get("capture_dir", cfg.capture_dir)
        cfg.dry_run = bool(runtime.get("dry_run", cfg.dry_run))
        if runtime.get("proxy"):
            cfg.proxy = dict(runtime["proxy"])

        cfg.credentials = dict(data.get("credentials", {}) or {})
        for key, env in ENV_CREDENTIALS.items():
            env_val = os.getenv(env)
            if env_val:
                cfg.credentials[key] = env_val
            elif isinstance(cfg.credentials.get(key), str) and cfg.credentials[key].startswith("env:"):
                # `env:SOME_VAR` indirection, so the committed file holds no secret.
                cfg.credentials[key] = os.getenv(cfg.credentials[key][4:], "")

        cfg.provider_options = {k: dict(v or {})
                                for k, v in (data.get("providers", {}) or {}).items()}
        cfg.alerts = AlertConfig.from_dict(data.get("alerts", {}) or {})
        return cfg

    def options_for(self, provider: str) -> dict[str, Any]:
        """Per-provider options, with the runtime-wide ones folded in."""
        opts = dict(self.provider_options.get(provider, {}))
        opts.setdefault("capture_dir", self.capture_dir)
        opts.setdefault("headless", self.headless)
        if self.proxy:
            opts.setdefault("proxy", self.proxy)
        return opts


def _resolve_path(path: str | Path | None) -> Optional[Path]:
    if path:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"config file not found: {p}")
        return p
    for candidate in DEFAULT_CONFIG_PATHS:
        p = Path(candidate)
        if p.exists():
            return p
    return None
