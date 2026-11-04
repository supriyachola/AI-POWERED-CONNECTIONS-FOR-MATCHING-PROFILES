import importlib
import os
import pytest


def reload_config(**env):
    keys = ["APP_ENV", "JWT_SECRET", "DATABASE_URL", "CORS_ORIGINS"]
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update(env)
        import app.config as cfg
        importlib.reload(cfg)
        return type('Snap', (), {k: getattr(cfg, k) for k in ('IS_PRODUCTION', 'CORS_ORIGINS', 'DATABASE_URL')})
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        import app.config as cfg
        importlib.reload(cfg)


GOOD = dict(JWT_SECRET="x" * 40, DATABASE_URL="postgresql://u:p@h/db", CORS_ORIGINS="https://affinityplus.vercel.app")


def test_unset_env_defaults_to_production_and_refuses_missing_secret():
    with pytest.raises(Exception, match="JWT_SECRET"):
        reload_config(DATABASE_URL=GOOD["DATABASE_URL"], CORS_ORIGINS=GOOD["CORS_ORIGINS"])


@pytest.mark.parametrize("secret", ["dev-secret-change-me", "short", "replace-with-a-long-random-secret"])
def test_weak_or_placeholder_secrets_rejected(secret):
    with pytest.raises(Exception, match="JWT_SECRET"):
        reload_config(**{**GOOD, "JWT_SECRET": secret})


def test_production_requires_postgres_and_explicit_cors():
    with pytest.raises(Exception, match="SQLite"):
        reload_config(**{**GOOD, "DATABASE_URL": "sqlite:///x.db"})
    with pytest.raises(Exception, match="DATABASE_URL"):
        reload_config(JWT_SECRET=GOOD["JWT_SECRET"], CORS_ORIGINS=GOOD["CORS_ORIGINS"])
    with pytest.raises(Exception, match="CORS_ORIGINS"):
        reload_config(JWT_SECRET=GOOD["JWT_SECRET"], DATABASE_URL=GOOD["DATABASE_URL"])
    with pytest.raises(Exception, match="CORS_ORIGINS"):
        reload_config(**{**GOOD, "CORS_ORIGINS": "*"})


def test_valid_production_config_loads():
    cfg = reload_config(**GOOD)
    assert cfg.IS_PRODUCTION and cfg.CORS_ORIGINS == ["https://affinityplus.vercel.app"]
