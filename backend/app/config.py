"""Central configuration. Fails fast in production when security settings are missing."""
import os
import secrets
import logging

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("affinity.config")

PLACEHOLDER_SECRETS = {"dev-secret-change-me", "replace-with-a-long-random-secret", "changeme", "secret"}


class ConfigError(RuntimeError):
    pass


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


# Unset APP_ENV is treated as production on purpose: a forgotten variable must fail
# closed (refuse weak config) rather than silently run with development defaults.
APP_ENV = (_get("APP_ENV", "production") or "production").lower()
IS_PRODUCTION = APP_ENV in {"production", "staging"}


def _jwt_secret() -> str:
    value = _get("JWT_SECRET")
    if IS_PRODUCTION:
        if not value or len(value) < 32 or value.lower() in PLACEHOLDER_SECRETS:
            raise ConfigError(
                "JWT_SECRET must be set to a random string of at least 32 characters "
                "(e.g. `python -c \"import secrets;print(secrets.token_urlsafe(48))\"`)."
            )
        return value
    if not value or value.lower() in PLACEHOLDER_SECRETS:
        log.warning("JWT_SECRET not set: using a random per-process secret (dev/test only).")
        return secrets.token_urlsafe(48)
    return value


def _database_url() -> str:
    value = _get("DATABASE_URL")
    if IS_PRODUCTION:
        if not value:
            raise ConfigError("DATABASE_URL is required in production (no SQLite fallback).")
        if value.startswith("sqlite"):
            raise ConfigError("SQLite is not allowed in production. Use PostgreSQL.")
        return value
    return value or "sqlite:///./connectai.db"


def _cors_origins() -> list[str]:
    raw = _get("CORS_ORIGINS")
    if IS_PRODUCTION and not raw:
        raise ConfigError("CORS_ORIGINS is required in production (comma-separated list of exact origins).")
    origins = [o.strip() for o in (raw or "http://localhost:5173").split(",") if o.strip()]
    if IS_PRODUCTION and "*" in origins:
        raise ConfigError("CORS_ORIGINS must not contain '*' in production.")
    return origins


JWT_SECRET = _jwt_secret()
DATABASE_URL = _database_url()
CORS_ORIGINS = _cors_origins()

# Access token lifetime. Stays long until refresh-session rotation ships (Phase 2);
# lower it as soon as refresh tokens exist.
ACCESS_TOKEN_MINUTES = int(_get("ACCESS_TOKEN_MINUTES", str(7 * 24 * 60)))
WS_TICKET_SECONDS = 60

# Call lifecycle timeouts (seconds)
RESERVED_TTL = int(_get("RESERVED_TTL", "30"))
RINGING_TTL = int(_get("RINGING_TTL", "30"))
ACCEPTED_TTL = int(_get("ACCEPTED_TTL", "30"))      # accepted -> someone must join the room
CONNECTING_TTL = int(_get("CONNECTING_TTL", "45"))  # both joined -> media must be confirmed
DISCONNECTED_GRACE = int(_get("DISCONNECTED_GRACE", "30"))
ABANDONED_AFTER = int(_get("ABANDONED_AFTER", "120"))  # live call with nobody online

# Discovery cool-downs (hours)
SKIP_COOLDOWN_HOURS = int(_get("SKIP_COOLDOWN_HOURS", "168"))
REPEAT_COOLDOWN_HOURS = int(_get("REPEAT_COOLDOWN_HOURS", "24"))
