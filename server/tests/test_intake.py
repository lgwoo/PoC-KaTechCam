"""입력 판정 — 자체 분류와 Moderation 두 판단을 합치는 자리.

둘이 어긋날 때 무엇이 이기는지가 전부다. 자체 분류가 이기면 위험 발화를 놓치고,
Moderation 만 믿으면 한국어 맥락을 놓친다. 그래서 "격상만 하고 강등은 없다"는
규칙이 서 있는지, 그리고 판정이 아예 실패했을 때 방어망이 남는지를 본다.
"""

from __future__ import annotations

from llm.client import StageRecorder
from llm.contracts import IntakeContract
from pipeline.intake import run_intake
from state.models import (
    GoalProgress,
    IntakeCategory,
    ModerationSeverity,
)
from tests.conftest import (
    CLEAN,
    FakeLlmClient,
    FakeModerationClient,
    TEST_SCENARIO,
    call_error,
    contract_error,
    flagged,
)


def contract(category: str = "NORMAL", **fields: object) -> IntakeContract:
    payload: dict = {"category": category, "reason": "테스트"}
    payload.update(fields)
    return IntakeContract(**payload)


async def run(intake_reply: object, *, moderation: FakeModerationClient | None = None,
              utterance: str = "친구가 넘어졌어"):
    client = FakeLlmClient({"intake": [intake_reply]})
    return await run_intake(
        client=client,
        moderation=moderation or FakeModerationClient(outcomes=[CLEAN]),
        recorder=StageRecorder(),
        scenario=TEST_SCENARIO,
        recent_history="마스코트: 오늘은 친구 마음을 알아보자.",
        focus_goal=TEST_SCENARIO.micro_goals[0],
        utterance=utterance,
    )


# ------------------------------------------------------------- 정상 경로


async def test_contract_values_pass_through() -> None:
    record, outcome = await run(
        contract("OFF_TOPIC", misunderstanding=True, character_bind=True,
                 goal_progress=GoalProgress.AWAY)
    )

    assert record.category is IntakeCategory.OFF_TOPIC
    assert record.misunderstanding is True
    assert record.character_bind is True
    assert record.goal_progress is GoalProgress.AWAY
    assert record.degraded is False
    assert record.override_source is None
    assert outcome.available is True


async def test_pii_keeps_the_masked_text() -> None:
    record, _ = await run(contract("PII", masked_text="내 이름은 [이름]이야"))

    assert record.category is IntakeCategory.PII
    assert record.masked_text == "내 이름은 [이름]이야"


async def test_masked_text_is_dropped_for_non_pii() -> None:
    """PII 가 아닌데 마스킹본을 들고 있으면, 저장 단계가 원문 대신 그것을 넣을 수 있다."""
    record, _ = await run(contract("NORMAL", masked_text="쓸데없는 마스킹"))
    assert record.masked_text is None


# --------------------------------------------------- 판정이 실패했을 때


async def test_call_failure_falls_back_to_normal_and_marks_degraded() -> None:
    """분류기가 죽었다고 역할극을 멈추지는 않는다. 대신 degraded 를 남겨서
    이 턴의 분류를 믿을 수 없다는 걸 기록에 박는다."""
    record, _ = await run(call_error("intake"))

    assert record.category is IntakeCategory.NORMAL
    assert record.degraded is True
    assert "인테이크 판정 실패" in record.reason


async def test_contract_failure_is_the_same_arm() -> None:
    record, _ = await run(contract_error("intake"))

    assert record.category is IntakeCategory.NORMAL
    assert record.degraded is True


async def test_unexpected_exception_is_also_degraded() -> None:
    """LlmError 계열이 아닌 예외까지 잡는다 — 여기서 새면 턴 전체가 500 이 된다."""
    record, _ = await run(RuntimeError("생각 못 한 것"))

    assert record.degraded is True
    assert record.category is IntakeCategory.NORMAL


async def test_degraded_turn_forces_neutral_progress() -> None:
    """진행 방향을 모르는데 TOWARD 로 두면 도움 세기가 제자리에 멈춘다.
    NEUTRAL 이어야 사다리가 계속 올라간다."""
    record, _ = await run(call_error("intake"))
    assert record.goal_progress is GoalProgress.NEUTRAL


async def test_degraded_with_clean_moderation_says_so() -> None:
    """분류기는 죽었지만 Moderation 은 살아 있었다는 사실이 사유에 남아야 한다 —
    방어망이 몇 겹 남았는지가 사후 판단의 근거다."""
    record, _ = await run(call_error("intake"))
    assert "Moderation 도 이상 없음" in record.reason


# ------------------------------------------------------- 격상만, 강등 없음


async def test_critical_forces_risk_over_the_llm_verdict() -> None:
    record, _ = await run(
        contract("NORMAL"),
        moderation=FakeModerationClient(
            outcomes=[flagged("self-harm/intent", severity=ModerationSeverity.CRITICAL)]
        ),
    )

    assert record.category is IntakeCategory.RISK
    assert record.override_source is not None
    assert "CRITICAL" in record.reason


async def test_high_escalates_to_high_concern() -> None:
    record, _ = await run(
        contract("NORMAL"),
        moderation=FakeModerationClient(
            outcomes=[flagged("violence/graphic", severity=ModerationSeverity.HIGH)]
        ),
    )

    assert record.category is IntakeCategory.HIGH_CONCERN
    assert record.override_source is not None


async def test_high_does_not_downgrade_risk() -> None:
    """RISK 는 중단으로 이어진다. HIGH 가 그것을 HIGH_CONCERN 으로 내리면
    멈춰야 할 대화가 계속된다."""
    record, _ = await run(
        contract("RISK"),
        moderation=FakeModerationClient(
            outcomes=[flagged("sexual", severity=ModerationSeverity.HIGH)]
        ),
    )

    assert record.category is IntakeCategory.RISK
    assert record.override_source is None


async def test_moderate_never_escalates() -> None:
    """넘어지고 다치는 시나리오는 정상적으로 violence 를 건드린다.
    여기서 격상하면 정상 대화가 계속 막힌다."""
    record, _ = await run(
        contract("NORMAL"),
        moderation=FakeModerationClient(
            outcomes=[flagged("violence", severity=ModerationSeverity.MODERATE)]
        ),
    )

    assert record.category is IntakeCategory.NORMAL
    assert record.override_source is None


async def test_degraded_plus_critical_still_halts() -> None:
    """분류기가 죽어도 Moderation 하나로 위험을 잡는다 — 방어망이 겹인 이유다."""
    record, _ = await run(
        call_error("intake"),
        moderation=FakeModerationClient(
            outcomes=[flagged("sexual/minors", severity=ModerationSeverity.CRITICAL)]
        ),
    )

    assert record.category is IntakeCategory.RISK
    assert record.degraded is True


async def test_pii_escalated_to_high_concern_loses_its_mask() -> None:
    """알려진 엣지다. 마스킹 여부를 격상 후 분류로 판단하기 때문에, PII 가
    HIGH_CONCERN 으로 올라가면 마스킹본이 버려지고 원문이 그대로 저장된다.
    고치기 전까지 이 테스트가 그 사실을 붙잡아 둔다."""
    record, _ = await run(
        contract("PII", masked_text="내 이름은 [이름]이야"),
        moderation=FakeModerationClient(
            outcomes=[flagged("harassment/threatening", severity=ModerationSeverity.HIGH)]
        ),
    )

    assert record.category is IntakeCategory.HIGH_CONCERN
    assert record.masked_text is None


# ---------------------------------------------- Moderation 이 없을 때


async def test_moderation_unavailable_is_recorded_not_assumed_safe() -> None:
    record, outcome = await run(
        contract("NORMAL"), moderation=FakeModerationClient(available=False)
    )

    assert outcome.available is False
    assert outcome.severity is ModerationSeverity.NONE
    assert record.category is IntakeCategory.NORMAL
    assert record.override_source is None


async def test_moderation_exception_does_not_break_the_turn() -> None:
    record, outcome = await run(
        contract("NORMAL"),
        moderation=FakeModerationClient(raises=RuntimeError("OpenAI 죽음")),
    )

    assert outcome.available is False
    assert record.category is IntakeCategory.NORMAL


# ------------------------------------------------------------- 계측 기록


async def test_both_stages_are_timed() -> None:
    """두 판단이 병렬로 돈다. 둘 다 기록돼야 리포트에서 겹침이 보인다."""
    client = FakeLlmClient({"intake": [contract()]})
    recorder = StageRecorder()
    moderation = FakeModerationClient(outcomes=[CLEAN])

    await run_intake(
        client=client,
        moderation=moderation,
        recorder=recorder,
        scenario=TEST_SCENARIO,
        recent_history="",
        focus_goal=None,
        utterance="친구가 넘어졌어",
    )

    assert {t.stage for t in recorder.timings} == {"intake", "moderation.input"}
    assert moderation.checked == [("moderation.input", ["친구가 넘어졌어"])]


async def test_prompt_says_no_goal_when_all_goals_are_done() -> None:
    """목표가 없으면 OFF_TOPIC 판단 기준이 달라진다 — 유도할 목표가 없는데
    '딴 얘기'로 몰면 마무리 대화가 전부 제지당한다."""
    client = FakeLlmClient({"intake": [contract()]})

    await run_intake(
        client=client, moderation=FakeModerationClient(outcomes=[CLEAN]),
        recorder=StageRecorder(), scenario=TEST_SCENARIO, recent_history="",
        focus_goal=None, utterance="고마워",
    )

    assert "목표를 모두 확인했다" in client.calls[0]["system"]
