"""환경 설정. .env 에서만 읽는다 — 키를 코드에 넣지 않는다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

SERVER_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(SERVER_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    elice_base_url: str
    elice_api_key: str
    elice_model: str
    moderation_api_key: str | None
    moderation_model: str
    max_output_tokens: int
    db_path: Path

    @property
    def moderation_available(self) -> bool:
        return bool(self.moderation_api_key)


def load_settings() -> Settings:
    base_url = os.environ.get("ELICE_MLAPI_BASE_URL", "").strip()
    api_key = os.environ.get("ELICE_MLAPI_API_KEY", "").strip()
    model = os.environ.get("ELICE_MODEL", "").strip()

    missing = [
        name
        for name, value in (
            ("ELICE_MLAPI_BASE_URL", base_url),
            ("ELICE_MLAPI_API_KEY", api_key),
            ("ELICE_MODEL", model),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f".env 에 다음이 비어 있다: {', '.join(missing)}. "
            f"scripts/verify_connection.py 로 값을 확인할 수 있다."
        )

    db_path = Path(os.environ.get("ET_DB_PATH", "neuringo_poc.db"))
    if not db_path.is_absolute():
        db_path = SERVER_ROOT / db_path

    return Settings(
        elice_base_url=base_url,
        elice_api_key=api_key,
        elice_model=model,
        # Moderation 은 진짜 OpenAI 키여야 한다. Elice 키와 절대 공유하지 않는다.
        moderation_api_key=(os.environ.get("OPENAI_MODERATION_API_KEY") or "").strip() or None,
        moderation_model=os.environ.get("OPENAI_MODERATION_MODEL", "omni-moderation-latest"),
        max_output_tokens=int(os.environ.get("MAX_OUTPUT_TOKENS", "2048")),
        db_path=db_path,
    )
