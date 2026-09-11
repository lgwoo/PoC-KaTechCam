"""실행 조건 스냅샷 — 지표를 비교할 수 있게 만드는 꼬리표.

이게 없으면 "이 수치는 어느 버전에서 잰 것인가"에 답할 수 없다. 실제로 초기 295턴은
프롬프트가 여러 번 바뀌는 동안 한 덩어리로 쌓여서 되짚을 방법이 없다.

여기서 지키는 두 가지: 같은 조건이면 같은 id 일 것(안 그러면 표본을 못 묶는다),
그리고 키가 절대 섞여 들어가지 않을 것.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app import snapshot
from db import repo


@dataclass(frozen=True)
class _Settings:
    """load_settings() 가 주는 것 중 스냅샷이 읽는 것만."""

    elice_base_url: str = "https://mlapi.run/abc123/v1"
    elice_model: str = "claude-haiku-4-5"
    moderation_model: str = "omni-moderation-latest"
    max_output_tokens: int = 2048
    elice_api_key: str = "sk-secret-key-do-not-store"
    moderation_api_key: str | None = "sk-openai-secret"

    @property
    def moderation_available(self) -> bool:
        return bool(self.moderation_api_key)


def _collect(settings: _Settings | None = None, **overrides) -> dict:
    base = dict(
        llm_timeout_s=60.0,
        sdk_max_retries=2,
        max_candidates=3,
        repair_attempts=1,
    )
    base.update(overrides)
    return snapshot.collect(settings or _Settings(), **base)


def test_same_conditions_give_the_same_id() -> None:
    """서버를 다시 띄웠다고 새 조건이 되면 안 된다 — 표본이 기동 횟수만큼 쪼개진다."""
    assert _collect()["snapshot_id"] == _collect()["snapshot_id"]


def test_changing_the_model_changes_the_id() -> None:
    other = _Settings(elice_model="claude-sonnet-5")
    assert _collect()["snapshot_id"] != _collect(other)["snapshot_id"]


def test_changing_max_tokens_changes_the_id() -> None:
    """출력 상한은 지연과 비용에 직접 걸린다. 같은 조건으로 묶으면 비교가 틀어진다."""
    other = _Settings(max_output_tokens=1024)
    assert _collect()["snapshot_id"] != _collect(other)["snapshot_id"]


def test_turning_moderation_off_changes_the_id() -> None:
    """방어망 한 겹이 빠진 실행을 켜진 실행과 같은 표본에 담으면 안 된다."""
    off = _Settings(moderation_api_key=None)
    assert _collect(off)["moderation_model"] is None
    assert _collect()["snapshot_id"] != _collect(off)["snapshot_id"]


def test_api_keys_never_enter_the_snapshot() -> None:
    """스냅샷은 DB 에 그대로 들어가고 리포트에도 실린다. 키가 섞이면 그대로 샌다."""
    fields = _collect()
    blob = repr(sorted(fields.items()))
    assert "sk-secret-key-do-not-store" not in blob
    assert "sk-openai-secret" not in blob
    # base_url 은 호스트만 남는다 — 경로에 엔드포인트 id 가 들어 있다.
    assert fields["base_url_host"] == "mlapi.run"
    assert "abc123" not in blob


def test_prompts_hash_is_read_from_the_real_file() -> None:
    """프롬프트가 바뀌면 판정 통과율이 움직인다. 그 경계가 id 에 반영돼야 한다."""
    fields = _collect()
    assert fields["prompts_sha"] != "unknown"
    assert snapshot.PROMPTS_PATH.name == "prompts.py"


def test_fields_match_the_table_columns(tmp_path: Path) -> None:
    """FIELDS 순서로 INSERT 한다. 컬럼을 추가하고 여기를 안 고치면 조용히 어긋난다."""
    conn = repo.connect(tmp_path / "t.db")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(CONFIG_SNAPSHOT)")}
    assert set(snapshot.FIELDS) <= columns
    conn.close()


def test_snapshot_row_is_written_once_per_condition(tmp_path: Path) -> None:
    conn = repo.connect(tmp_path / "t.db")
    fields = _collect()

    first = repo.upsert_config_snapshot(conn, fields)
    second = repo.upsert_config_snapshot(conn, fields)

    assert first == second
    assert conn.execute("SELECT count(*) FROM CONFIG_SNAPSHOT").fetchone()[0] == 1
    conn.close()


def test_a_second_condition_adds_a_row(tmp_path: Path) -> None:
    conn = repo.connect(tmp_path / "t.db")
    repo.upsert_config_snapshot(conn, _collect())
    repo.upsert_config_snapshot(conn, _collect(_Settings(elice_model="other")))

    assert conn.execute("SELECT count(*) FROM CONFIG_SNAPSHOT").fetchone()[0] == 2
    conn.close()
