"""실행 조건 스냅샷.

지표는 "어떤 조건에서 잰 값인가"가 붙어야 비교할 수 있다. 프롬프트 한 줄만 고쳐도
판정 통과율과 재생성률이 움직이는데, 지금까지 그 경계가 DB 어디에도 없었다 —
초기 295턴은 프롬프트가 여러 번 바뀌는 나흘 동안 한 덩어리로 쌓여서, 어느 수치가
어느 버전의 것인지 되짚을 방법이 없다.

snapshot_id 는 아래 값들의 해시다. 조건이 같으면 같은 id 가 나오므로 세션을
snapshot_id 로 묶는 것이 곧 "같은 조건에서 잰 표본"을 고르는 일이 된다.

키는 절대 담지 않는다. base_url 도 호스트만 남긴다.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

SERVER_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_PATH = SERVER_ROOT / "pipeline" / "prompts.py"

# CONFIG_SNAPSHOT 의 컬럼 순서와 같다. repo 가 이 순서로 INSERT 한다.
FIELDS: tuple[str, ...] = (
    "snapshot_id",
    "git_sha",
    "git_dirty",
    "prompts_sha",
    "agent_model",
    "moderation_model",
    "base_url_host",
    "max_output_tokens",
    "llm_timeout_s",
    "sdk_max_retries",
    "max_candidates",
    "repair_attempts",
    "python_version",
    "platform",
    "in_docker",
)


def _git(*args: str) -> str | None:
    """git 이 없거나(도커 이미지) 저장소가 아니면 None. 실행을 막지는 않는다."""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=SERVER_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _file_sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "unknown"


def identity(fields: dict) -> str:
    """내용 해시. 시각은 넣지 않는다 — 넣으면 매 기동이 새 조건이 되어 묶을 수 없다."""
    payload = json.dumps(
        {k: v for k, v in fields.items() if k != "snapshot_id"},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def collect(
    settings,
    *,
    llm_timeout_s: float,
    sdk_max_retries: int | None,
    max_candidates: int,
    repair_attempts: int,
) -> dict:
    """지금 이 프로세스가 어떤 조건으로 도는지 한 벌 모은다."""
    git_sha = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain")

    fields: dict = {
        "git_sha": git_sha[:12] if git_sha else None,
        "git_dirty": None if porcelain is None else int(bool(porcelain)),
        "prompts_sha": _file_sha(PROMPTS_PATH),
        "agent_model": settings.elice_model,
        # 키가 없으면 Moderation 없이 도는 실행이다. 그 사실 자체가 조건이다.
        "moderation_model": (
            settings.moderation_model if settings.moderation_available else None
        ),
        "base_url_host": urlsplit(settings.elice_base_url).hostname or "",
        "max_output_tokens": settings.max_output_tokens,
        "llm_timeout_s": llm_timeout_s,
        "sdk_max_retries": sdk_max_retries,
        "max_candidates": max_candidates,
        "repair_attempts": repair_attempts,
        "python_version": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "in_docker": int(Path("/.dockerenv").exists()),
    }
    fields["snapshot_id"] = identity(fields)
    return fields
