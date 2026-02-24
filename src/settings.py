import os
from dataclasses import dataclass
from pathlib import Path


GEMINI_MODEL_ENV = "GEMINI_MODEL"
GEMINI_MODEL_DEFAULT = "gemini-2.5-flash"

API_SECRET_ENV = "API_SECRET"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_env(dotenv_path: str | os.PathLike[str] | None = None) -> None:
    """Load .env into process environment (non-overriding)."""
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    path = Path(dotenv_path) if dotenv_path is not None else (project_root() / ".env")
    load_dotenv(dotenv_path=path, override=False)


@dataclass(frozen=True)
class GeminiConfig:
    model: str


def get_gemini_config() -> GeminiConfig:
    load_env()
    model = os.getenv(GEMINI_MODEL_ENV, GEMINI_MODEL_DEFAULT)
    return GeminiConfig(model=model)


def get_api_secret() -> str:
    secret = os.getenv(API_SECRET_ENV)
    if not secret:
        load_env()
        secret = os.getenv(API_SECRET_ENV)
    if not secret:
        raise RuntimeError(f"{API_SECRET_ENV} env var is not set")
    return secret
