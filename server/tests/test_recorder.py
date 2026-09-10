"""단계 계측기와 구조 응답 수리 — 실측 리포트가 서 있는 바닥.

STAGE_TIMING 이 곧 성능 리포트의 원본이다. 여기가 틀리면 리포트 숫자 전부가 틀리고,
더 나쁜 건 조용히 틀린다는 것이다. 실패한 왕복이 기록에서 사라지거나, 수리 재시도가
본체 시간에 합쳐지면 '어디가 느린가'를 볼 수 없다.
"""

from __future__ import annotations

import pytest

from llm.client import LlmClient, StageRecorder, _unwrap_envelope, _validate
from llm.contracts import IntakeContract, JudgeContract
from llm.errors import LlmCallError, LlmContractError

# --------------------------------------------------------------- StageRecorder


def test_span_records_stage_and_attempt() -> None:
    recorder = StageRecorder()
    with recorder.span("generate", 2):
        pass

    assert len(recorder.timings) == 1
    timing = recorder.timings[0]
    assert timing.stage == "generate"
    assert timing.attempt_no == 2
    assert timing.ok is True


def test_span_marks_failure_and_reraises() -> None:
    """예외를 삼키면 안 된다 — 호출부가 실패를 알아야 degraded 로 기록한다.
    동시에 기록은 남아야 한다. 실패한 왕복도 시간을 썼기 때문이다."""
    recorder = StageRecorder()

    with pytest.raises(RuntimeError):
        with recorder.span("intake"):
            raise RuntimeError("boom")

    assert len(recorder.timings) == 1
    assert recorder.timings[0].ok is False


def test_offsets_are_measured_from_the_turn_start() -> None:
    """오프셋이 있어야 무엇이 병렬로 돌았는지 보인다. 순서대로 쌓인 값만으로는
    소요 합계가 전체 시간을 넘는 이유를 설명할 수 없다."""
    recorder = StageRecorder()
    with recorder.span("a"):
        pass
    with recorder.span("b"):
        pass

    first, second = recorder.timings
    assert first.start_offset_ms == 0
    assert second.start_offset_ms >= first.start_offset_ms
    assert recorder.total_ms() >= second.start_offset_ms


def test_attempt_no_defaults_to_none() -> None:
    """시도 번호가 없는 단계(인테이크·목표 스캔)와 있는 단계(생성·판정)를 구분한다."""
    recorder = StageRecorder()
    with recorder.span("goal_scan"):
        pass
    assert recorder.timings[0].attempt_no is None


# ------------------------------------------------------------------ base_url


def test_base_url_must_end_with_v1() -> None:
    """SDK 가 base_url 뒤에 /chat/completions 만 붙인다. /v1 이 없으면 전부 404 가 되고,
    그건 키 문제처럼 보여서 엉뚱한 곳을 뒤지게 된다."""
    with pytest.raises(ValueError, match="/v1"):
        LlmClient(api_key="k", base_url="https://example.com", model="m")


def test_trailing_slash_is_allowed() -> None:
    client = LlmClient(api_key="k", base_url="https://example.com/v1/", model="m")
    assert client.model == "m"


# --------------------------------------------------------------- 구조 검증


def test_code_fence_is_stripped() -> None:
    """모델이 ```json 으로 감싸 보내는 일이 실제로 있다."""
    parsed, detail = _validate(
        '```json\n{"category": "NORMAL", "reason": "ok"}\n```', IntakeContract
    )
    assert parsed is not None, detail
    assert parsed.category == "NORMAL"


def test_broken_json_reports_a_readable_reason() -> None:
    parsed, detail = _validate("{nope", IntakeContract)
    assert parsed is None
    assert "JSON" in detail


def test_missing_field_names_the_field() -> None:
    """수리 재시도 프롬프트에 이 문장이 그대로 들어간다. 읽을 수 없으면 수리도 못 한다."""
    parsed, detail = _validate('{"reason": "이유만 있다"}', IntakeContract)
    assert parsed is None
    assert "category" in detail


def test_single_wrapper_key_is_unwrapped() -> None:
    """claude-haiku-4-5 가 {"output": {...}} 로 한 겹 싸서 보내는 일이 있다."""
    payload = _unwrap_envelope(
        {"output": {"safe_to_send": True, "decision": "PASS"}}, JudgeContract
    )
    assert payload == {"safe_to_send": True, "decision": "PASS"}


def test_real_field_name_is_not_unwrapped() -> None:
    """계약에 있는 이름이면 감싼 게 아니라 값이 하나뿐인 응답이다. 벗기면 내용이 사라진다."""
    payload = _unwrap_envelope({"decision": "PASS"}, JudgeContract)
    assert payload == {"decision": "PASS"}


# ------------------------------------------------------- 수리 재시도 계약


class _ScriptedClient(LlmClient):
    """_call 만 바꿔 끼운다. structured() 의 수리 흐름은 실제 코드를 그대로 태운다 —
    그 흐름이 검증 대상이라서 흉내내면 의미가 없다."""

    def __init__(self, replies: list[object]) -> None:
        super().__init__(api_key="dummy", base_url="https://example.com/v1", model="m")
        self._replies = list(replies)
        self.call_count = 0

    async def _call(self, *, system: str, user: str, json_mode: bool) -> str:
        self.call_count += 1
        item = self._replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return str(item)


GOOD = '{"category": "NORMAL", "reason": "ok"}'


async def test_valid_first_response_makes_one_call_and_one_span() -> None:
    client = _ScriptedClient([GOOD])
    recorder = StageRecorder()

    parsed = await client.structured(
        stage="intake", recorder=recorder, system="s", user="u", contract=IntakeContract
    )

    assert parsed.category == "NORMAL"
    assert client.call_count == 1
    assert [t.stage for t in recorder.timings] == ["intake"]


async def test_malformed_response_gets_exactly_one_repair() -> None:
    """수리는 한 번뿐이다. 무한 재시도로 늘리면 한 턴이 몇 분씩 걸린다."""
    client = _ScriptedClient(["{nope", GOOD])
    recorder = StageRecorder()

    parsed = await client.structured(
        stage="intake", recorder=recorder, system="s", user="u", contract=IntakeContract
    )

    assert parsed.category == "NORMAL"
    assert client.call_count == 2
    assert [t.stage for t in recorder.timings] == ["intake", "intake.repair"]


async def test_two_bad_responses_raise_contract_error() -> None:
    client = _ScriptedClient(["{nope", "{still nope"])

    with pytest.raises(LlmContractError) as caught:
        await client.structured(
            stage="intake", recorder=StageRecorder(), system="s", user="u",
            contract=IntakeContract,
        )

    assert caught.value.stage == "intake"
    assert client.call_count == 2


async def test_first_response_records_ok_even_when_the_body_is_invalid() -> None:
    """검증은 스팬 밖에서 돈다. 왕복 자체는 성공했으니 ok=True 이고, 형식 위반은
    별도의 .repair 스팬으로 드러난다 — 둘을 합치면 네트워크 실패와 구분이 안 된다."""
    client = _ScriptedClient(["{nope", GOOD])
    recorder = StageRecorder()

    await client.structured(
        stage="intake", recorder=recorder, system="s", user="u", contract=IntakeContract
    )

    assert [(t.stage, t.ok) for t in recorder.timings] == [
        ("intake", True),
        ("intake.repair", True),
    ]


async def test_network_failure_is_a_call_error_with_a_failed_span() -> None:
    client = _ScriptedClient([RuntimeError("연결 실패")])
    recorder = StageRecorder()

    with pytest.raises(LlmCallError) as caught:
        await client.structured(
            stage="judge", recorder=recorder, system="s", user="u",
            contract=JudgeContract,
        )

    assert caught.value.stage == "judge"
    assert [(t.stage, t.ok) for t in recorder.timings] == [("judge", False)]


async def test_repair_failure_is_attributed_to_the_repair_stage() -> None:
    """어느 왕복이 죽었는지 이름으로 구분돼야 한다."""
    client = _ScriptedClient(["{nope", RuntimeError("두 번째도 실패")])
    recorder = StageRecorder()

    with pytest.raises(LlmCallError) as caught:
        await client.structured(
            stage="generate", recorder=recorder, system="s", user="u",
            contract=IntakeContract, attempt_no=2,
        )

    assert caught.value.stage == "generate.repair"
    assert [t.stage for t in recorder.timings] == ["generate", "generate.repair"]


async def test_repair_prompt_carries_the_offending_output() -> None:
    """무엇이 틀렸는지 안 알려주면 모델이 같은 실수를 반복한다."""
    seen: list[str] = []

    class _Watching(_ScriptedClient):
        async def _call(self, *, system: str, user: str, json_mode: bool) -> str:
            seen.append(user)
            return await super()._call(system=system, user=user, json_mode=json_mode)

    client = _Watching(["{nope", GOOD])
    await client.structured(
        stage="intake", recorder=StageRecorder(), system="s", user="원래 프롬프트",
        contract=IntakeContract,
    )

    assert len(seen) == 2
    assert "{nope" in seen[1]
    assert "원래 프롬프트" in seen[1]


async def test_attempt_no_is_carried_into_the_span() -> None:
    """후보 3회 루프에서 어느 시도가 느렸는지 보려면 시도 번호가 붙어 있어야 한다."""
    client = _ScriptedClient([GOOD])
    recorder = StageRecorder()

    await client.structured(
        stage="generate", recorder=recorder, system="s", user="u",
        contract=IntakeContract, attempt_no=3,
    )

    assert recorder.timings[0].attempt_no == 3
