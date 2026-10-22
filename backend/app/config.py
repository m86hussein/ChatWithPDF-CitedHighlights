import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if not v else v.lower() in ("1", "true", "yes", "on")


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(BASE_DIR / "data"))))
    database_url: str = _env("DATABASE_URL")
    jwt_secret: str = _env("JWT_SECRET")
    jwt_ttl_hours: int = int(_env("JWT_TTL_HOURS", "12"))
    cookie_secure: bool = _bool("COOKIE_SECURE", False)
    allow_registration: bool = _bool("ALLOW_REGISTRATION", True)
    cors_origins: list[str] = field(default_factory=lambda: [o for o in _env("CORS_ORIGINS").split(",") if o])
    max_upload_mb: int = int(_env("MAX_UPLOAD_MB", "200"))

    # storage: "local" or "s3"
    storage_backend: str = _env("STORAGE_BACKEND", "local")
    s3_bucket: str = _env("S3_BUCKET")
    s3_prefix: str = _env("S3_PREFIX", "documents/")
    s3_kms_key_id: str = _env("S3_KMS_KEY_ID")

    # vector DB: empty QDRANT_URL => embedded local Qdrant under data_dir
    qdrant_url: str = _env("QDRANT_URL")
    qdrant_api_key: str = _env("QDRANT_API_KEY")
    qdrant_collection: str = _env("QDRANT_COLLECTION", "pdf_chunks")

    # AWS Bedrock
    aws_profile: str = _env("BEDROCK_AWS_PROFILE")
    aws_region: str = _env("BEDROCK_REGION", "us-east-1")
    embed_model: str = _env("EMBED_MODEL", "us.cohere.embed-v4:0")
    embed_dim: int = int(_env("EMBED_DIM", "1536"))
    chat_model: str = _env("CHAT_MODEL", "us.anthropic.claude-sonnet-4-6")
    stats_model: str = _env("STATS_MODEL", "us.anthropic.claude-sonnet-4-6")
    rerank_region: str = _env("RERANK_REGION", "us-west-2")
    rerank_model: str = _env("RERANK_MODEL", "cohere.rerank-v3-5:0")

    # pipeline switches
    ocr_languages: str = _env("OCR_LANGUAGES", "ara+eng")
    ocr_dpi: int = int(_env("OCR_DPI", "300"))
    # OCR Arabic pages whose text layer has visual-order ligatures instead of repairing them heuristically
    ocr_broken_arabic: bool = _bool("OCR_BROKEN_ARABIC", False)
    stats_llm: bool = _bool("STATS_LLM", True)
    chart_vision: bool = _bool("CHART_VISION", True)
    chart_vision_max: int = int(_env("CHART_VISION_MAX", "25"))
    ingest_workers: int = int(_env("INGEST_WORKERS", "2"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)

if not settings.jwt_secret:
    # Dev convenience: persist a random secret so sessions survive restarts. Set JWT_SECRET in production.
    _secret_file = settings.data_dir / ".jwt_secret"
    if not _secret_file.exists():
        _secret_file.write_text(secrets.token_urlsafe(48))
        _secret_file.chmod(0o600)
    object.__setattr__(settings, "jwt_secret", _secret_file.read_text().strip())

if not settings.database_url:
    object.__setattr__(settings, "database_url", f"sqlite:///{settings.data_dir / 'app.db'}")
