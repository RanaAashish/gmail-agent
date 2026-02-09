from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class GmailConfig(BaseSettings):
    """Central configuration — can come from .env, environment variables or defaults"""

    # Gmail API
    scopes: list[str] = Field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
        ]
    )

    credentials_file: Path = Path("credentials.json")
    token_file: Path = Path("token.json")

    # Storage
    save_dir: Path = Path("saved_emails")
    max_fetch: int = Field(default=10, ge=10, le=500)

    # Thread naming / identification
    thread_prefix: str = "gmail-clean-"

    @field_validator("save_dir", mode="before")
    @classmethod
    def ensure_save_dir(cls, v):
        path = Path(v)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @field_validator("credentials_file", "token_file", mode="before")
    @classmethod
    def to_path(cls, v):
        return Path(v)


CONFIG = GmailConfig()