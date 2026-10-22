from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:  # Keeps core modules importable in minimal test environments.
    from pydantic import BaseModel as BaseSettings

    class SettingsConfigDict(dict):
        pass


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"] = "development"
    api_key: str = "change-me"
    admin_api_key: str = "change-admin-me"
    expose_api_docs: bool = True
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    json_logs: bool = True
    replica_count: int = Field(default=1, ge=1, le=100)

    docs_root: Path = Path("data/docs")
    index_dir: Path = Path("data/index")
    max_context_chars: int = 30_000
    retrieval_top_k: int = 36
    rerank_top_k: int = 12
    reranker_candidate_k: int = Field(default=12, ge=1, le=100)
    graph_depth: int = 1

    distributed_knowledge_enabled: bool = False
    distributed_fail_open: bool = True
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_ttl_seconds: int = 900
    redis_ingest_lock_seconds: int = Field(default=3_600, ge=30)
    snapshot_consistency_required: bool = True
    local_retained_snapshots: int = Field(default=3, ge=2, le=20)
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "nl2sql_schema_artifacts"
    qdrant_timeout_seconds: float = 10.0
    qdrant_batch_size: int = 128
    qdrant_retained_snapshots: int = 3
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    neo4j_database: str = "neo4j"
    neo4j_timeout_seconds: float = 10.0

    embedding_provider: Literal["sentence_transformer", "feature_hash"] = "sentence_transformer"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_revision: str = ""
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32

    reranker_provider: Literal["cross_encoder", "lexical"] = "cross_encoder"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_revision: str = "b5160aeac3c6c8fe7beaaaf04c9e0142826b58d1"
    reranker_device: str = "cpu"
    reranker_batch_size: int = Field(default=2, ge=1, le=16)
    reranker_max_length: int = Field(default=256, ge=128, le=1024)
    reranker_warmup: bool = True
    reranker_onnx_model: str = "/model-cache/repo"
    reranker_onnx_revision: str = "a5b99697a0220ebf15e9f1f9954df76d1108b9cc"
    reranker_onnx_file: str = "model_int8.onnx"

    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_API_KEY", "NL2SQL_OPENAI_API_KEY"),
    )
    openai_api_key_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY_FILE", "NL2SQL_OPENAI_API_KEY_FILE"),
    )
    llm_provider: Literal[
        "openai", "gapgpt", "deepseek", "qwen", "openai_compatible"
    ] = "openai"
    llm_api_key: str = Field(
        default="",
        validation_alias="NL2SQL_LLM_API_KEY",
    )
    llm_api_key_file: Path | None = Field(
        default=None,
        validation_alias="NL2SQL_LLM_API_KEY_FILE",
    )
    deepseek_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "NL2SQL_DEEPSEEK_API_KEY"),
    )
    deepseek_api_key_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "DEEPSEEK_API_KEY_FILE",
            "NL2SQL_DEEPSEEK_API_KEY_FILE",
        ),
    )
    qwen_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DASHSCOPE_API_KEY", "NL2SQL_QWEN_API_KEY"),
    )
    qwen_api_key_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "DASHSCOPE_API_KEY_FILE",
            "NL2SQL_QWEN_API_KEY_FILE",
        ),
    )
    gapgpt_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GAPGPT_API_KEY", "NL2SQL_GAPGPT_API_KEY"),
    )
    gapgpt_api_key_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GAPGPT_API_KEY_FILE",
            "NL2SQL_GAPGPT_API_KEY_FILE",
        ),
    )
    gapgpt_base_url: str = "https://api.gapgpt.app/v1"
    qwen_base_url: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_openai_models: list[str] = ["gpt-5.6-terra"]
    llm_gapgpt_models: list[str] = ["gapgpt-qwen-3.5"]
    llm_deepseek_models: list[str] = ["deepseek-v4-flash", "deepseek-v4-pro"]
    llm_qwen_models: list[str] = ["qwen3.7-plus"]
    llm_compatible_models: list[str] = []
    llm_compatible_api_key: str = ""
    llm_compatible_base_url: str = ""
    llm_reasoning_effort: Literal["none", "low", "medium", "high", "max"] = "low"
    llm_thinking_enabled: bool = False
    llm_timeout_seconds: float = 45.0
    max_repair_attempts: int = 1
    llm_required: bool = False

    allow_execution: bool = False
    database_url: str = ""
    database_url_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("NL2SQL_DATABASE_URL_FILE", "DATABASE_URL_FILE"),
    )
    query_timeout_seconds: int = 15
    lock_timeout_ms: int = 5_000
    max_rows: int = 200
    strict_schema_validation: bool = True
    role_policy_path: Path | None = None

    jwt_enabled: bool = False
    jwt_algorithm: Literal["HS256", "RS256"] = "HS256"
    jwt_secret: str = ""
    jwt_public_key: str = ""
    jwt_issuer: str = ""
    jwt_audience: str = ""
    default_api_role: str = "report_analyst"
    rate_limit_per_minute: int = 60
    audit_enabled: bool = True
    audit_db_path: Path = Path("data/audit.db")
    operational_database_url: str = ""
    operational_auto_create: bool = False
    require_migration_head: bool = False
    expected_migration_revision: str = "20260717_0003"
    audit_hmac_key: str = "change-audit-hmac-key"
    cors_origins: list[str] = ["http://localhost:5173"]

    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NL2SQL_",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    @model_validator(mode="after")
    def validate_security_settings(self):
        self.openai_api_key = self._secret_value(
            self.openai_api_key,
            self.openai_api_key_file,
            "OPENAI_API_KEY_FILE",
        )
        self.llm_api_key = self._secret_value(
            self.llm_api_key,
            self.llm_api_key_file,
            "NL2SQL_LLM_API_KEY_FILE",
        )
        self.deepseek_api_key = self._secret_value(
            self.deepseek_api_key,
            self.deepseek_api_key_file,
            "DEEPSEEK_API_KEY_FILE",
        )
        self.qwen_api_key = self._secret_value(
            self.qwen_api_key,
            self.qwen_api_key_file,
            "DASHSCOPE_API_KEY_FILE",
        )
        self.gapgpt_api_key = self._secret_value(
            self.gapgpt_api_key,
            self.gapgpt_api_key_file,
            "GAPGPT_API_KEY_FILE",
        )
        self.database_url = self._secret_value(
            self.database_url,
            self.database_url_file,
            "NL2SQL_DATABASE_URL_FILE",
        )
        if self.jwt_enabled and self.jwt_algorithm == "HS256" and len(self.jwt_secret) < 32:
            raise ValueError("NL2SQL_JWT_SECRET must contain at least 32 characters for HS256")
        if self.jwt_enabled and self.jwt_algorithm == "RS256" and not self.jwt_public_key:
            raise ValueError("NL2SQL_JWT_PUBLIC_KEY is required for RS256")
        if self.allow_execution and not self.database_url:
            raise ValueError("NL2SQL_DATABASE_URL is required when execution is enabled")
        if self.llm_provider == "openai_compatible" and not self.llm_base_url:
            raise ValueError(
                "NL2SQL_LLM_BASE_URL is required when "
                "NL2SQL_LLM_PROVIDER=openai_compatible"
            )
        if self.llm_required and not self.resolved_llm_model:
            raise ValueError(
                "NL2SQL_LLM_MODEL is required for the selected LLM provider"
            )
        if self.llm_required and (
            not self.resolved_llm_api_key
            or self.resolved_llm_api_key.lower().startswith("replace")
        ):
            raise ValueError(
                "An API key for NL2SQL_LLM_PROVIDER is required when "
                "NL2SQL_LLM_REQUIRED=true"
            )
        if self.environment == "production":
            violations = self.production_violations()
            if violations:
                raise ValueError("Invalid production configuration: " + "; ".join(violations))
        return self

    def production_violations(self) -> list[str]:
        issues: list[str] = []
        if (
            self.api_key == "change-me"
            or self.api_key.lower().startswith("replace")
            or len(self.api_key) < 24
        ):
            issues.append("NL2SQL_API_KEY must be a non-default secret of at least 24 characters")
        if (
            self.admin_api_key == "change-admin-me"
            or self.admin_api_key.lower().startswith("replace")
            or len(self.admin_api_key) < 24
        ):
            issues.append("NL2SQL_ADMIN_API_KEY must be a non-default secret of at least 24 characters")
        if self.api_key == self.admin_api_key:
            issues.append("query and admin API keys must be different")
        if self.expose_api_docs:
            issues.append("NL2SQL_EXPOSE_API_DOCS must be false")
        if not self.trusted_hosts:
            issues.append("NL2SQL_TRUSTED_HOSTS must contain at least one host")
        if (
            self.audit_hmac_key == "change-audit-hmac-key"
            or self.audit_hmac_key.lower().startswith("replace")
            or len(self.audit_hmac_key) < 32
        ):
            issues.append("NL2SQL_AUDIT_HMAC_KEY must be a non-default secret of at least 32 characters")
        if not self.distributed_knowledge_enabled:
            issues.append("distributed knowledge must be enabled")
        if self.distributed_fail_open:
            issues.append("NL2SQL_DISTRIBUTED_FAIL_OPEN must be false")
        if not self.snapshot_consistency_required:
            issues.append("NL2SQL_SNAPSHOT_CONSISTENCY_REQUIRED must be true")
        if self.embedding_provider == "sentence_transformer" and not self.embedding_revision:
            issues.append("NL2SQL_EMBEDDING_REVISION must pin an immutable model revision")
        if self.reranker_provider == "cross_encoder" and not self.reranker_revision:
            issues.append("NL2SQL_RERANKER_REVISION must pin an immutable model revision")
        if self.reranker_provider == "cross_encoder" and not self.reranker_onnx_revision:
            issues.append("NL2SQL_RERANKER_ONNX_REVISION must pin an immutable ONNX revision")
        if not self.llm_required:
            issues.append("NL2SQL_LLM_REQUIRED must be true")
        if self.role_policy_path is None:
            issues.append("NL2SQL_ROLE_POLICY_PATH must point to a versioned allowlist")
        if self.qdrant_api_key.lower().startswith("replace") or len(self.qdrant_api_key) < 24:
            issues.append("NL2SQL_QDRANT_API_KEY must contain at least 24 characters")
        if "@" not in self.redis_url or "replace" in self.redis_url.lower():
            issues.append("NL2SQL_REDIS_URL must include authentication")
        if (
            self.neo4j_password == "password"
            or self.neo4j_password.lower().startswith("replace")
            or len(self.neo4j_password) < 16
        ):
            issues.append("NL2SQL_NEO4J_PASSWORD must be a non-default secret")
        if self.operational_auto_create:
            issues.append("NL2SQL_OPERATIONAL_AUTO_CREATE must be false")
        if "*" in self.cors_origins:
            issues.append("wildcard CORS origin is not allowed")
        if "*" in self.trusted_hosts:
            issues.append("wildcard trusted host is not allowed")
        if self.replica_count > 1 and self.resolved_operational_database_url.startswith("sqlite"):
            issues.append("SQLite operational storage cannot be used with multiple replicas")
        if self.allow_execution and not self.strict_schema_validation:
            issues.append("strict schema validation is required when execution is enabled")
        if self.require_migration_head is False:
            issues.append("NL2SQL_REQUIRE_MIGRATION_HEAD must be true")
        return issues

    @staticmethod
    def _secret_value(current: str, path: Path | None, setting_name: str) -> str:
        if current:
            return current
        if path is None:
            return ""
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"{setting_name} could not be read") from exc
        if not value:
            raise ValueError(f"{setting_name} is empty")
        return value

    @property
    def resolved_operational_database_url(self) -> str:
        if self.operational_database_url:
            return self.operational_database_url
        path = self.audit_db_path.expanduser().resolve()
        return f"sqlite:///{path.as_posix()}"

    @property
    def resolved_llm_api_key(self) -> str:
        return self.llm_api_key_for(self.llm_provider)

    def llm_api_key_for(self, provider: str) -> str:
        if provider == self.llm_provider and self.llm_api_key:
            return self.llm_api_key
        if provider == "openai":
            return self.openai_api_key
        if provider == "gapgpt":
            return self.gapgpt_api_key
        if provider == "deepseek":
            return self.deepseek_api_key
        if provider == "qwen":
            return self.qwen_api_key
        if provider == "openai_compatible":
            return self.llm_compatible_api_key
        return ""

    @property
    def resolved_llm_base_url(self) -> str:
        return self.llm_base_url_for(self.llm_provider)

    def llm_base_url_for(self, provider: str) -> str:
        if provider == self.llm_provider and self.llm_base_url:
            return self.llm_base_url.rstrip("/")
        if provider == "qwen" and self.qwen_base_url:
            return self.qwen_base_url.rstrip("/")
        if provider == "gapgpt" and self.gapgpt_base_url:
            return self.gapgpt_base_url.rstrip("/")
        if provider == "openai_compatible" and self.llm_compatible_base_url:
            return self.llm_compatible_base_url.rstrip("/")
        return {
            "openai": "",
            "gapgpt": "https://api.gapgpt.app/v1",
            "deepseek": "https://api.deepseek.com",
            "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "openai_compatible": "",
        }[provider]

    @property
    def resolved_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return {
            "openai": "gpt-5.6-terra",
            "gapgpt": "gapgpt-qwen-3.5",
            "deepseek": "deepseek-v4-flash",
            "qwen": "qwen3.7-plus",
            "openai_compatible": "",
        }[self.llm_provider]

    def llm_models_for(self, provider: str) -> list[str]:
        configured = {
            "openai": self.llm_openai_models,
            "gapgpt": self.llm_gapgpt_models,
            "deepseek": self.llm_deepseek_models,
            "qwen": self.llm_qwen_models,
            "openai_compatible": self.llm_compatible_models,
        }[provider]
        models = [item.strip() for item in configured if item.strip()]
        if provider == self.llm_provider and self.resolved_llm_model:
            models.insert(0, self.resolved_llm_model)
        return list(dict.fromkeys(models))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
