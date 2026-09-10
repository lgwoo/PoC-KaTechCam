"""4스테이지 오케스트레이터 — 조합이 실제로 일어나는 자리.

부품은 각각 테스트돼 있었지만 이 파일이 없어서 조립 결과는 아무도 안 봤다. 여기서
보는 것은 부품의 동작이 아니라 "합쳤을 때 무엇이 기록에 남고 무엇이 남지 않는가"다.
특히 중단 경로는 아이 발화를 기록에서 빼는데, 그 판단이 어긋나면 위험 발화가
다음 턴의 대화 맥락으로 들어간다.
"""

from __future__ import annotations

from llm.contracts import (
    AchievedClaim,
    GoalScanContract,
    IntakeContract,
    JudgeContract,
    TurnReplyContract,
)
from pipeline.turn import MASCOT_FAREWELL, run_turn
from state.models import (
    EndReason,
    GoalObservationStatus,
    GoalProgress,
    HintSource,
    IntakeCategory,
    MascotMode,
    ModerationSeverity,
    SessionPhase,
    SessionStatus,
    Speaker,
    SupportLevel,
)
from tests.conftest import (
    CLEAN,
    TEST_SCENARIO,
    FakeLlmClient,
    FakeModerationClient,
    call_error,
    flagged,
    make_session,
)

# MG-01 의 근거는 "넘어졌다는 것을 언급한다" 다. 목표 스캔이 인정하려면 스캐너가
# 고른 근거 문구가 아이 발화 안에 실제로 있어야 한다(pipeline/goals.py 네 번째 관문).
CHILD_LINE = "친구가 넘어져서 아팠을 것 같아"
EVIDENCE = "넘어져서 아팠을"

SAFE_REPLY = "응... 무릎이 좀 그래. 너는 어때?"


def intake(category: str = "NORMAL", **fields: object) -> IntakeContract:
    payload: dict = {"category": category, "reason": "테스트"}
    payload.update(fields)
    return IntakeContract(**payload)


def scan(*goal_ids: str, evidence: str = EVIDENCE) -> GoalScanContract:
    return GoalScanContract(
        achieved=[
            AchievedClaim(
                micro_goal_id=goal_id,
                evidence=evidence,
                why_this_satisfies="아이가 사건과 기분을 함께 말했다",
            )
            for goal_id in goal_ids
        ]
    )


def reply(character: str = SAFE_REPLY, mascot: str | None = None) -> TurnReplyContract:
    return TurnReplyContract(character_line=character, mascot_line=mascot)


PASSING = JudgeContract(safe_to_send=True, decision="PASS", reason="ok")


async def turn(
    *,
    intake_reply: object = None,
    scan_reply: object = None,
    generate_reply: object = None,
    judge_reply: object = None,
    moderation: FakeModerationClient | None = None,
    scenario=TEST_SCENARIO,
    session=None,
    utterance: str = CHILD_LINE,
):
    """준비된 응답만 큐에 넣는다 — 넣지 않은 단계가 호출되면 FakeLlmClient 가
    소리 내어 실패한다. 중단 경로가 생성까지 가지 않는다는 걸 그렇게 확인한다."""
    responses: dict[str, list[object]] = {"intake": [intake_reply or intake()]}
    if scan_reply is not None:
        responses["goal_scan"] = [scan_reply]
    if generate_reply is not None:
        responses["generate"] = [generate_reply]
    if judge_reply is not None:
        responses["judge"] = [judge_reply]

    client = FakeLlmClient(responses)
    session = session if session is not None else make_session(scenario)
    record = await run_turn(
        client=client,
        moderation=moderation or FakeModerationClient(outcomes=[CLEAN]),
        scenario=scenario,
        session=session,
        utterance=utterance,
    )
    return record, session, client


def normal_turn(**overrides: object):
    kwargs: dict = {
        "scan_reply": scan("MG-01"),
        "generate_reply": reply(),
        "judge_reply": PASSING,
    }
    kwargs.update(overrides)
    return turn(**kwargs)


# --------------------------------------------------------------- 중단 경로


async def test_risk_input_halts_before_generation() -> None:
    """생성을 아예 부르지 않는다. 부르면 위험 발화를 문맥에 넣은 채로 LLM 을 태운다."""
    record, session, client = await turn(intake_reply=intake("RISK"))

    assert record.mascot_mode is MascotMode.HALT
    assert record.end_reason is EndReason.SAFETY_HALT
    assert record.phase_after is SessionPhase.ENDED
    assert session.phase is SessionPhase.ENDED
    assert client.stages_called() == ["intake"]


async def test_halted_child_utterance_never_enters_the_transcript() -> None:
    """SAFE-06. 여기 남으면 다음 턴 프롬프트에 그 문장이 그대로 실린다."""
    _, session, _ = await turn(intake_reply=intake("RISK"), utterance="위험한 말")

    assert not any(u.speaker is Speaker.CHILD for u in session.transcript)
    assert all("위험한 말" != u.text for u in session.transcript)


async def test_halt_leaves_only_the_mascot_line() -> None:
    record, session, _ = await turn(intake_reply=intake("RISK"))

    assert record.mascot_line
    assert record.delivered_text is None
    assert record.candidates == []
    assert record.fallback_used is False
    assert record.hint_source is HintSource.NONE
    assert session.transcript[-1].speaker is Speaker.MASCOT


async def test_moderation_critical_halts_even_if_intake_said_normal() -> None:
    record, _, client = await turn(
        intake_reply=intake("NORMAL"),
        moderation=FakeModerationClient(
            outcomes=[flagged("self-harm/intent", severity=ModerationSeverity.CRITICAL)]
        ),
    )

    assert record.end_reason is EndReason.SAFETY_HALT
    assert client.stages_called() == ["intake"]


async def test_halt_records_only_the_input_stages() -> None:
    """중단 턴의 소요 시간이 정상 턴만큼 나오면 리포트가 거짓이 된다."""
    record, _, _ = await turn(intake_reply=intake("RISK"))

    assert {t.stage for t in record.timings} == {"intake", "moderation.input"}
    assert record.total_ms >= 0


async def test_farewell_covers_every_end_reason() -> None:
    """MASCOT_FAREWELL[end_reason] 은 조회 실패가 없어야 한다 — 종료 순간에
    KeyError 가 나면 세션이 인사도 없이 끊긴다."""
    for reason in EndReason:
        assert MASCOT_FAREWELL[reason]


# ------------------------------------------------------------ 개인정보


async def test_pii_is_stored_masked() -> None:
    record, session, _ = await normal_turn(
        intake_reply=intake("PII", masked_text="내 이름은 [이름]이야"),
        utterance="내 이름은 김민수야",
    )

    assert record.child_input == "내 이름은 김민수야"
    assert record.child_input_stored == "내 이름은 [이름]이야"
    child = [u for u in session.transcript if u.speaker is Speaker.CHILD]
    assert child[-1].text == "내 이름은 [이름]이야"


async def test_pii_escalated_to_high_concern_stores_the_raw_text() -> None:
    """알려진 엣지. Moderation 이 격상하면 분류가 PII 가 아니게 되고, 마스킹본이
    버려져서 원문이 그대로 기록과 DB 에 들어간다. 고칠 때까지 사실을 붙잡아 둔다."""
    record, session, _ = await normal_turn(
        intake_reply=intake("PII", masked_text="내 이름은 [이름]이야"),
        moderation=FakeModerationClient(
            outcomes=[flagged("harassment/threatening", severity=ModerationSeverity.HIGH)]
        ),
        utterance="내 이름은 김민수야",
    )

    assert record.intake.category is IntakeCategory.HIGH_CONCERN
    assert record.child_input_stored == "내 이름은 김민수야"
    assert any("김민수" in u.text for u in session.transcript)


# ------------------------------------------------------------ 힌트 출처


async def test_scaffold_turn_credits_the_mascot() -> None:
    session = make_session()
    session.goals["MG-01"].support_level = SupportLevel.S1

    record, _, _ = await normal_turn(
        session=session, generate_reply=reply(mascot="친구 표정이 어때 보여?")
    )

    assert record.mascot_mode is MascotMode.SCAFFOLD
    assert record.hint_source is HintSource.MASCOT


async def test_plain_turn_credits_the_character() -> None:
    """마스코트가 안 나서도 캐릭터가 목표 질문을 맡는다. 그것도 힌트로 센다 —
    안 세면 '스스로 도달' 지표가 부풀려진다."""
    record, _, _ = await normal_turn()

    assert record.mascot_mode is MascotMode.NONE
    assert record.hint_source is HintSource.CHARACTER


async def test_final_turn_stops_hinting() -> None:
    """마지막 턴은 마무리다. 여기서 새 목표를 유도하면 아이가 답할 턴이 없다."""
    scenario = TEST_SCENARIO.model_copy(deep=True)
    scenario.maximum_turn_count = 1

    record, _, _ = await normal_turn(scenario=scenario)

    assert record.hint_source is HintSource.NONE


async def test_no_open_goal_means_no_hint() -> None:
    session = make_session(
        **{
            "MG-01": GoalObservationStatus.ACHIEVED,
            "MG-02": GoalObservationStatus.ACHIEVED,
            "MG-03": GoalObservationStatus.ACHIEVED,
        }
    )

    record, _, client = await turn(
        session=session, generate_reply=reply(), judge_reply=PASSING
    )

    assert record.hint_source is HintSource.NONE
    assert record.focus_goal_id is None
    # 열린 목표가 없으면 스캐너를 부르지 않는다 — 부를 이유가 없는 왕복이다.
    assert "goal_scan" not in client.stages_called()
    assert record.goal_scan.scanned_goal_ids == []


# ------------------------------------------------------------ 목표 확정


async def test_accepted_claim_marks_the_goal_achieved() -> None:
    record, session, _ = await normal_turn()

    assert record.goal_scan.accepted == ["MG-01"]
    goal = session.goals["MG-01"]
    assert goal.status is GoalObservationStatus.ACHIEVED
    assert goal.achieved_at_turn == 1
    assert goal.evidence_turn_index == 1
    assert goal.evidence_text == EVIDENCE
    assert goal.why_this_satisfies


async def test_achievement_after_an_answer_reveal_is_marked_as_assisted() -> None:
    """S3 에서 답을 알려 준 뒤 아이가 따라 말한 것을 '스스로 도달'로 세면
    PoC 성공률이 허구가 된다."""
    session = make_session()
    session.goals["MG-01"].support_level = SupportLevel.S3

    _, session, _ = await normal_turn(
        session=session, generate_reply=reply(mascot="친구 표정이 어때 보여?")
    )

    assert session.goals["MG-01"].achieved_with_support is True


async def test_unassisted_achievement_is_not_marked() -> None:
    _, session, _ = await normal_turn()
    assert session.goals["MG-01"].achieved_with_support is False


async def test_evidence_absent_from_the_child_line_is_rejected() -> None:
    """스캐너가 캐릭터 대사를 근거로 인용하는 일이 있었다. 아이가 말하지 않은 것을
    달성으로 세면 지표가 통째로 무너진다."""
    record, session, _ = await normal_turn(scan_reply=scan("MG-01", evidence="아이가 한 적 없는 말"))

    assert record.goal_scan.accepted == []
    assert len(record.goal_scan.rejected) == 1
    assert "아이 발화에 실제로 없음" in record.goal_scan.rejected[0].reason
    assert session.goals["MG-01"].status is GoalObservationStatus.NOT_OBSERVED


async def test_scan_failure_confirms_nothing_but_still_replies() -> None:
    """스캐너가 죽어도 대화는 이어진다. 다만 이 턴에는 어떤 목표도 인정되지 않는다."""
    record, session, _ = await normal_turn(scan_reply=call_error("goal_scan"))

    assert "goal_scan" in record.degraded_stages
    assert record.goal_scan.accepted == []
    assert record.goal_scan.scanned_goal_ids == ["MG-01", "MG-02", "MG-03"]
    assert record.delivered_text == SAFE_REPLY
    assert session.goals["MG-01"].status is GoalObservationStatus.NOT_OBSERVED


async def test_degraded_stages_accumulate() -> None:
    record, _, _ = await normal_turn(
        intake_reply=call_error("intake"), scan_reply=call_error("goal_scan")
    )

    assert record.degraded_stages == ["intake", "goal_scan"]
    assert record.intake.degraded is True


# ------------------------------------------------------------ 도움 세기


async def test_accepted_focus_goal_does_not_burn_a_support_turn() -> None:
    """맞힌 아이에게 도움 세기를 올릴 이유가 없다. 올리면 다음 목표를 시작하기도
    전에 예산이 줄어든다."""
    record, session, _ = await normal_turn(scan_reply=scan("MG-01"))
    assert session.goals["MG-01"].support_turns_used == 0
    assert record.support_changes == []


async def test_focus_goal_not_accepted_burns_a_support_turn() -> None:
    """인정받지 못한 턴에만 사다리가 올라간다 — 맞힌 아이에게 더 알려 줄 이유가 없다."""
    record, session, _ = await normal_turn(scan_reply=GoalScanContract(achieved=[]))

    assert session.goals["MG-01"].support_turns_used == 1
    assert session.goals["MG-01"].support_level is SupportLevel.S1
    assert [c.to_level for c in record.support_changes] == [SupportLevel.S1]


async def test_progressing_child_holds_the_level() -> None:
    """목표로 가고 있으면 세기를 올리지 않는다. 올리면 같은 유도를 계속 듣는다."""
    record, session, _ = await normal_turn(
        intake_reply=intake("NORMAL", goal_progress=GoalProgress.TOWARD),
        scan_reply=GoalScanContract(achieved=[]),
    )

    assert session.goals["MG-01"].support_level is SupportLevel.S0
    assert session.goals["MG-01"].status is GoalObservationStatus.PARTIAL
    assert record.support_changes == []


async def test_support_budget_exhaustion_abandons_the_goal() -> None:
    session = make_session()
    session.goals["MG-01"].support_turns_used = 3
    session.goals["MG-01"].support_level = SupportLevel.S3

    record, session, _ = await normal_turn(
        session=session,
        scan_reply=GoalScanContract(achieved=[]),
        generate_reply=reply(mascot="친구 표정이 어때 보여?"),
    )

    assert session.goals["MG-01"].status is GoalObservationStatus.ABANDONED
    # 포기에는 세기 변경 기록이 붙지 않는다 — 올라간 게 아니라 접은 것이다.
    assert record.support_changes == []


# --------------------------------------------------------------- 종료 판정


async def test_all_goals_ends_the_session() -> None:
    session = make_session(
        **{"MG-02": GoalObservationStatus.ACHIEVED, "MG-03": GoalObservationStatus.ACHIEVED}
    )

    record, session, _ = await normal_turn(session=session)

    assert record.end_reason is EndReason.ALL_GOALS
    assert session.phase is SessionPhase.ENDED
    assert record.closing_line == MASCOT_FAREWELL[EndReason.ALL_GOALS]


async def test_turn_cap_beats_all_goals() -> None:
    """일부러 그렇게 뒀다. 상한에 닿은 턴은 목표를 다 채웠어도 상한으로 끝났다고
    기록한다 — 상한 때문에 잘린 세션과 목표를 다 채운 세션을 섞으면 지표가 흐려진다."""
    scenario = TEST_SCENARIO.model_copy(deep=True)
    scenario.maximum_turn_count = 1
    session = make_session(
        scenario,
        **{"MG-02": GoalObservationStatus.ACHIEVED, "MG-03": GoalObservationStatus.ACHIEVED},
    )

    record, _, _ = await normal_turn(scenario=scenario, session=session)

    assert record.goal_scan.accepted == ["MG-01"]
    assert record.end_reason is EndReason.TURN_CAP


async def test_closing_line_is_separate_from_the_mascot_line() -> None:
    """같은 필드에 넣으면 리포트에서 '유도한 말'과 '작별 인사'가 구분되지 않는다."""
    session = make_session(
        **{"MG-02": GoalObservationStatus.ACHIEVED, "MG-03": GoalObservationStatus.ACHIEVED}
    )
    session.goals["MG-01"].support_level = SupportLevel.S1

    record, _, _ = await normal_turn(
        session=session, generate_reply=reply(mascot="친구 표정이 어때 보여?")
    )

    assert record.mascot_line == "친구 표정이 어때 보여?"
    assert record.closing_line == MASCOT_FAREWELL[EndReason.ALL_GOALS]


async def test_unfinished_session_stays_active() -> None:
    record, session, _ = await normal_turn(scan_reply=GoalScanContract(achieved=[]))

    assert record.end_reason is None
    assert record.phase_after is SessionPhase.ACTIVE
    assert session.phase is SessionPhase.ACTIVE


# ------------------------------------------------------------- 기록 자체


def turn_indexes(session) -> list[int]:
    return [u.turn_index for u in session.transcript]


async def test_turn_index_advances_once() -> None:
    session = make_session()
    await normal_turn(session=session, scan_reply=GoalScanContract(achieved=[]))
    assert session.turn_index == 1
    assert turn_indexes(session) == [0, 0, 1, 1]


async def test_transcript_order_is_child_mascot_character() -> None:
    """마스코트 대사가 캐릭터 대사보다 먼저 들어간다. 그래서 이번 턴 마스코트 대사가
    앵무새 관문의 대조 대상에 포함되고, 캐릭터 대사는 포함되지 않는다."""
    session = make_session()
    session.goals["MG-01"].support_level = SupportLevel.S1

    _, session, _ = await normal_turn(
        session=session, generate_reply=reply(mascot="친구 표정이 어때 보여?")
    )

    speakers = [u.speaker for u in session.transcript if u.turn_index == 1]
    assert speakers == [Speaker.CHILD, Speaker.MASCOT, Speaker.CHARACTER]


async def test_all_pipeline_stages_are_timed_on_a_normal_turn() -> None:
    record, _, _ = await normal_turn()

    assert {t.stage for t in record.timings} == {
        "moderation.input", "intake", "goal_scan", "generate", "judge", "moderation.output",
    }
    assert record.total_ms >= 0


async def test_parallel_stages_overlap() -> None:
    """소요 합계가 전체 시간보다 크다는 것이 병렬로 돌았다는 증거다. 이 관계가
    뒤집히면 어딘가에서 병렬 실행이 직렬로 바뀐 것이다."""
    record, _, _ = await normal_turn()

    total_of_stages = sum(t.duration_ms for t in record.timings)
    assert total_of_stages >= 0
    offsets = {t.stage: t.start_offset_ms for t in record.timings}
    assert offsets["intake"] <= offsets["generate"]
    assert offsets["goal_scan"] <= offsets["judge"]


async def test_candidates_and_delivery_are_recorded() -> None:
    record, _, _ = await normal_turn()

    assert len(record.candidates) == 1
    assert record.candidates[0].delivered is True
    assert record.delivered_text == SAFE_REPLY
    assert record.fallback_used is False


async def test_ladder_exhausted_needs_a_higher_turn_cap_than_we_ship() -> None:
    """출하 설정으로는 이 종료 사유에 닿을 수 없다.

    목표 3개 × 도움 4턴 = 12턴이 필요한데 시나리오 상한이 6이고, evaluate_end 는
    상한을 최우선으로 본다. 그래서 실제 플레이로는 LADDER_EXHAUSTED 가 절대 안 나온다 —
    시뮬레이션 스윕이 이 경로를 못 만드는 이유가 여기 있다. 상한만 올리면 나온다는 걸
    확인해서, 코드가 죽은 게 아니라 설정이 가린 것임을 분명히 해 둔다.
    """
    scenario = TEST_SCENARIO.model_copy(deep=True)
    scenario.maximum_turn_count = 99
    session = make_session(
        scenario,
        **{
            "MG-01": GoalObservationStatus.ABANDONED,
            "MG-02": GoalObservationStatus.ABANDONED,
        },
    )
    session.goals["MG-03"].support_turns_used = 3

    record, session, _ = await normal_turn(
        scenario=scenario,
        session=session,
        scan_reply=GoalScanContract(achieved=[]),
    )

    assert session.goals["MG-03"].status is GoalObservationStatus.ABANDONED
    assert record.end_reason is EndReason.LADDER_EXHAUSTED


async def test_ended_session_is_marked_completed() -> None:
    """status 는 세션 생성 때 IN_PROGRESS 로 한 번 쓰이고 아무도 갱신하지 않았다.
    그래서 끝난 세션 42개가 전부 "진행 중"으로 남아 있었다 — 이 컬럼을 믿고
    "지금 몇 세션이 돌고 있나"를 세면 전부 틀린다."""
    session = make_session(
        **{"MG-02": GoalObservationStatus.ACHIEVED, "MG-03": GoalObservationStatus.ACHIEVED}
    )

    record, session, _ = await normal_turn(session=session)

    assert record.end_reason is EndReason.ALL_GOALS
    assert session.status is SessionStatus.COMPLETED


async def test_safety_halt_also_marks_completed() -> None:
    """안전 중단도 성공은 아니지만 enum 에 그 값이 없다. 성공 여부는 end_reason 이
    들고 있으므로 status 는 "더 이상 돌지 않는다"만 표시한다."""
    _, session, _ = await turn(intake_reply=intake("RISK"))

    assert session.end_reason is EndReason.SAFETY_HALT
    assert session.status is SessionStatus.COMPLETED


async def test_unfinished_session_stays_in_progress() -> None:
    _, session, _ = await normal_turn(scan_reply=GoalScanContract(achieved=[]))

    assert session.end_reason is None
    assert session.status is SessionStatus.IN_PROGRESS
