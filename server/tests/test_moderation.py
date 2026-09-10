"""Moderation 은 절대 예외를 올리지 않는다 — 그 약속이 깨지면 역할극이 멈춘다.

이 파일이 보는 것은 "실패했을 때 어떻게 실패하는가"다. OpenAI 가 잠깐 죽었을 때
아이가 보는 화면이 멈추면 안 되고, 동시에 "확인 못 했음"이 조용히 "안전함"으로
바뀌어도 안 된다. 두 요구가 부딪히는 자리라서 분기마다 확인이 필요하다.
"""

from __future__ import annotations

from types import SimpleNamespace

from llm.client import StageRecorder
from safety import moderation as mod
from state.models import ModerationSeverity


class _Field:
    """SDK 응답 필드는 pydantic 모델이라 model_dump() 를 갖는다."""

    def __init__(self, values: dict) -> None:
        self._values = values

    def model_dump(self) -> dict:
        return dict(self._values)


def result(flagged: bool, **hits: float) -> SimpleNamespace:
    """flagged 카테고리와 점수를 SDK 표기(밑줄)로 만든다."""
    return SimpleNamespace(
        flagged=flagged,
        categories=_Field({name: True for name in hits}),
        category_scores=_Field(dict(hits)),
    )


class _Moderations:
    def __init__(self, results: list, *, raises: Exception | None = None) -> None:
        self._results = results
        self._raises = raises
        self.calls: list[list[str]] = []

    async def create(self, *, model: str, input: list[str]):  # noqa: A002 - SDK 이름
        self.calls.append(list(input))
        if self._raises is not None:
            raise self._raises
        return SimpleNamespace(results=self._results)


def client_with(results: list, *, raises: Exception | None = None) -> tuple:
    client = mod.ModerationClient(api_key="dummy")
    stub = _Moderations(results, raises=raises)
    client._client = SimpleNamespace(moderations=stub)
    return client, stub


# ------------------------------------------------------------ 키가 없을 때


async def test_no_key_reports_unavailable_not_safe() -> None:
    """키가 없으면 '이상 없음'이 아니라 '확인 못 했음'이어야 한다.
    둘을 같이 취급하면 방어망 한 겹이 빠진 실행을 통과한 실행으로 착각한다."""
    client = mod.ModerationClient(api_key=None)
    recorder = StageRecorder()

    outcome = await client.check("아무 말", stage="moderation.input", recorder=recorder)

    assert not client.available
    assert not outcome.available
    assert outcome.severity is ModerationSeverity.NONE
    assert not outcome.flagged


async def test_no_key_records_no_span() -> None:
    """호출을 안 했으면 단계 기록도 없어야 한다 — 0ms 짜리 가짜 단계가 리포트에 끼면
    '검열을 돌렸는데 순식간에 끝났다'로 읽힌다."""
    client = mod.ModerationClient(api_key=None)
    recorder = StageRecorder()

    await client.check_many(["a", "b"], stage="moderation.input", recorder=recorder)

    assert recorder.timings == []


async def test_unavailable_is_returned_per_input() -> None:
    client = mod.ModerationClient(api_key=None)
    outcomes = await client.check_many(
        ["a", "b", "c"], stage="moderation.output", recorder=StageRecorder()
    )
    assert len(outcomes) == 3


async def test_unavailable_singleton_is_shared_and_mutable() -> None:
    """키가 없을 때 돌려주는 것은 새 객체가 아니라 모듈 수준 상수 그 자체다.

    ModerationOutcome 은 frozen 이 아니라서 이 객체는 고칠 수 있다. 누가 판정 결과를
    받아서 제자리에서 고치면 그 뒤 모든 '확인 못 했음' 판정이 같이 바뀐다. 지금 코드에
    그렇게 하는 곳은 없지만, 공유되고 있다는 사실 자체가 테스트로 박혀 있어야
    나중에 누가 outcome.flagged = True 를 쓸 때 이 테스트가 근거가 된다."""
    client = mod.ModerationClient(api_key=None)

    first = await client.check("a", stage="s", recorder=StageRecorder())
    second = await client.check("b", stage="s", recorder=StageRecorder())

    assert first is mod.UNAVAILABLE
    assert first is second


# --------------------------------------------------------- 호출이 실패할 때


async def test_api_failure_does_not_raise() -> None:
    client, _ = client_with([], raises=RuntimeError("502 bad gateway"))
    recorder = StageRecorder()

    outcomes = await client.check_many(
        ["아무 말"], stage="moderation.input", recorder=recorder
    )

    assert [o.available for o in outcomes] == [False]


async def test_api_failure_still_records_the_span_as_failed() -> None:
    """실패한 왕복도 시간을 썼다. 기록이 없으면 리포트에서 그 시간이 사라진다."""
    client, _ = client_with([], raises=RuntimeError("boom"))
    recorder = StageRecorder()

    await client.check_many(["x"], stage="moderation.input", recorder=recorder)

    assert len(recorder.timings) == 1
    assert recorder.timings[0].stage == "moderation.input"
    assert recorder.timings[0].ok is False


async def test_length_mismatch_is_refused() -> None:
    """API 가 입력 2개에 결과 1개를 주면 순서를 믿을 수 없다. 그대로 쓰면 마스코트
    판정이 캐릭터 대사에 붙는다 — 판정이 뒤바뀌는 게 조용히 통과하는 것보다 나쁘다."""
    client, _ = client_with([result(True, violence=0.9)])
    outcomes = await client.check_many(
        ["캐릭터 대사", "마스코트 대사"], stage="moderation.output",
        recorder=StageRecorder(),
    )

    assert len(outcomes) == 2
    assert all(not o.available for o in outcomes)


async def test_two_texts_go_in_one_round_trip() -> None:
    """한 턴에 최대 3회 시도하므로, 두 대사를 따로 부르면 왕복 3번이 통째로 늘어난다."""
    client, stub = client_with([result(False), result(False)])
    await client.check_many(["a", "b"], stage="moderation.output", recorder=StageRecorder())

    assert stub.calls == [["a", "b"]]


# ------------------------------------------------------------- 결과 변환


async def test_sdk_names_become_api_names_but_scores_keep_the_raw_form() -> None:
    """카테고리 이름은 self_harm → self-harm 으로 되돌리고, 점수 키는 SDK 표기를
    그대로 둔다. 표기가 다른 건 의도다 — 점수는 원본을 확인하는 용도라서 그렇다."""
    client, _ = client_with([result(True, self_harm_intent=0.91)])

    outcome = (await client.check_many(["x"], stage="s", recorder=StageRecorder()))[0]

    assert outcome.categories == ["self-harm/intent"]
    assert outcome.category_scores == {"self_harm_intent": 0.91}
    assert outcome.severity is ModerationSeverity.CRITICAL


async def test_only_flagged_categories_get_scores() -> None:
    client, _ = client_with([result(True, violence=0.7)])
    outcome = (await client.check_many(["x"], stage="s", recorder=StageRecorder()))[0]

    assert list(outcome.category_scores) == ["violence"]
    assert outcome.severity is ModerationSeverity.MODERATE


async def test_clean_result_is_available_and_unflagged() -> None:
    client, _ = client_with([result(False)])
    outcome = (await client.check_many(["x"], stage="s", recorder=StageRecorder()))[0]

    assert outcome.available
    assert not outcome.flagged
    assert outcome.categories == []
    assert outcome.severity is ModerationSeverity.NONE


async def test_sexual_is_treated_as_high_for_a_child_service() -> None:
    """일반 서비스에서는 MODERATE 로 두는 축이다. 아동 대상이라 올려 둔 것이고,
    이게 내려가면 전달 차단선이 조용히 낮아진다."""
    client, _ = client_with([result(True, sexual=0.6)])
    outcome = (await client.check_many(["x"], stage="s", recorder=StageRecorder()))[0]

    assert outcome.severity is ModerationSeverity.HIGH
