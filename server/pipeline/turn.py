"""턴 오케스트레이션 — 4단계.

    STAGE 1  INTAKE       LLM 1콜 ‖ Moderation
    STAGE 2  결정적        severity 오버라이드 → 마스코트 모드
    STAGE 3  병렬          목표 스캔 ‖ (생성 → 판단 → 심판)
    STAGE 4  결정적        목표 확정 → 도움 세기 조정 → 종료 판정 → 기록

생성 1콜이 마스코트 대사와 캐릭터 대사를 **함께** 만든다. 나눠 부르면 두 화자가 서로를
모른 채 말해서 장면이 어긋난다.

순차 LLM 홉은 2개다(인테이크 → 생성/판단). TS 판과 같은 깊이인데, PII 마스킹이
인테이크 응답에 흡수돼서 오히려 한 홉 줄었다.
"""

from __future__ import annotations

import asyncio

from llm.client import LlmClient, StageRecorder
from llm.contracts import GoalScanContract
from llm.errors import LlmCallError, LlmContractError
from pipeline import generate, intake, mascot, prompts, support
from pipeline import goals as goals_module
from pipeline.goals import ScanClaim
from safety.moderation import ModerationClient
from state.models import (
    EndReason,
    GoalObservationStatus,
    GoalScanRecord,
    HintSource,
    IntakeCategory,
    MascotMode,
    MicroGoal,
    Scenario,
    SessionPhase,
    SessionStatus,
    SessionState,
    Speaker,
    SupportLevel,
    SupportLevelChange,
    TurnRecord,
    Utterance,
)

MASCOT_FAREWELL = {
    EndReason.ALL_GOALS: "멍멍! 오늘 친구 마음을 정말 잘 알아줬어. 스탬프 하나 줄게!",
    EndReason.TURN_CAP: "멍멍! 오늘은 여기까지 하자. 같이 이야기해 줘서 고마워!",
    EndReason.LADDER_EXHAUSTED: "멍멍! 오늘은 여기까지 하자. 다음에 또 같이 해보자!",
    EndReason.SAFETY_HALT: "오늘은 여기서 멈출게. 선생님이랑 이야기해 보자.",
}

_SPEAKER_LABEL = {Speaker.CHILD: "아이", Speaker.CHARACTER: "친구", Speaker.MASCOT: "마스코트"}


def _render(session: SessionState) -> str:
    return "\n".join(f"{_SPEAKER_LABEL[u.speaker]}: {u.text}" for u in session.transcript)


async def _scan_goals(
    *,
    client: LlmClient,
    recorder: StageRecorder,
    pending: list[MicroGoal],
    history: str,
) -> list[ScanClaim] | Exception:
    if not pending:
        return []
    try:
        result = await client.structured(
            stage="goal_scan",
            recorder=recorder,
            system=prompts.goal_scan_system(pending),
            user=history,
            contract=GoalScanContract,
        )
    except (LlmCallError, LlmContractError) as exc:
        return exc
    return [ScanClaim(c.micro_goal_id, c.evidence, c.why_this_satisfies) for c in result.achieved]


async def run_turn(
    *,
    client: LlmClient,
    moderation: ModerationClient,
    scenario: Scenario,
    session: SessionState,
    utterance: str,
) -> TurnRecord:
    recorder = StageRecorder()
    session.turn_index += 1

    focus_state = support.pick_focus_goal(session, scenario)
    focus_goal = scenario.goal(focus_state.goal_id) if focus_state else None
    support_level = focus_state.support_level if focus_state else SupportLevel.S0
    reveal_line = scenario.opening_prompts.get(SupportLevel.S3)

    history_before = _render(session)

    # ---------------------------------------------------------------- STAGE 1
    intake_record, moderation_outcome = await intake.run_intake(
        client=client,
        moderation=moderation,
        recorder=recorder,
        scenario=scenario,
        recent_history=history_before,
        focus_goal=focus_goal,
        utterance=utterance,
    )

    # ---------------------------------------------------------------- STAGE 2
    mode = mascot.select_mode(
        category=intake_record.category,
        severity=moderation_outcome.severity,
        misunderstanding=intake_record.misunderstanding,
        character_bind=intake_record.character_bind,
        support_level=support_level,
        has_open_goal=focus_state is not None,
        goal_progress=intake_record.goal_progress,
    )
    fallback_line = mascot.fallback_line(
        mode,
        category=intake_record.category,
        misunderstanding=intake_record.misunderstanding,
        scenario=scenario,
        support_level=support_level,
        focus_goal=focus_goal,
    )

    record = TurnRecord(
        session_id=session.session_id,
        turn_index=session.turn_index,
        child_input=utterance,
        child_input_stored=utterance,
        intake=intake_record,
        moderation=moderation_outcome,
        mascot_mode=mode,
        focus_goal_id=focus_goal.id if focus_goal else None,
        support_level=support_level,
    )
    if intake_record.degraded:
        record.degraded_stages.append("intake")

    # HALT — 보호 절차가 역할극보다 우선한다. 이 발화는 transcript 에 남기지 않는다(SAFE-06).
    if mode is MascotMode.HALT:
        line = fallback_line or MASCOT_FAREWELL[EndReason.SAFETY_HALT]
        record.mascot_line = line
        record.end_reason = EndReason.SAFETY_HALT
        record.phase_after = SessionPhase.ENDED
        session.phase = SessionPhase.ENDED
        session.status = SessionStatus.COMPLETED
        session.end_reason = EndReason.SAFETY_HALT
        session.transcript.append(
            Utterance(speaker=Speaker.MASCOT, text=line, turn_index=session.turn_index)
        )
        record.timings = recorder.timings
        record.total_ms = recorder.total_ms()
        return record

    # PII 마스킹본은 인테이크가 이미 같이 줬다 — 별도 콜이 없다.
    stored = utterance
    if intake_record.category is IntakeCategory.PII and intake_record.masked_text:
        stored = intake_record.masked_text
    record.child_input_stored = stored
    session.transcript.append(
        Utterance(speaker=Speaker.CHILD, text=stored, turn_index=session.turn_index)
    )

    history_with_child = _render(session)
    open_goal_ids = {g.goal_id for g in session.open_goals()}
    pending = [g for g in scenario.micro_goals if g.id in open_goal_ids]

    # 턴 상한은 스캔 전에 이미 결정적으로 안다 — 마지막 턴이면 새 질문 대신 마무리 톤으로 만든다.
    is_final_turn = session.turn_index >= scenario.maximum_turn_count

    character_hints = mascot.character_gets_goal_hint(mode) and not is_final_turn
    # 누가 유도했는지는 여기서 결정된다. 프롬프트에만 반영하고 넘어가면 나중에 "아이가 도움을
    # 몇 번 받았나"를 셀 수 없다 — 마스코트 개입만 세면 S0 의 캐릭터 유도를 통째로 놓친다.
    if mode is MascotMode.SCAFFOLD:
        record.hint_source = HintSource.MASCOT
    elif character_hints and focus_goal is not None:
        record.hint_source = HintSource.CHARACTER
    else:
        record.hint_source = HintSource.NONE

    # ---------------------------------------------------------------- STAGE 3 (병렬)
    scan_result, generation = await asyncio.gather(
        _scan_goals(
            client=client, recorder=recorder, pending=pending, history=history_with_child
        ),
        generate.generate_approved_reply(
            client=client,
            moderation=moderation,
            recorder=recorder,
            scenario=scenario,
            history=history_with_child,
            child_input=stored,
            focus_goal=None if is_final_turn else focus_goal,
            reveal_line=reveal_line,
            recent_character_lines=session.lines_by(Speaker.CHARACTER, last_n_turns=2),
            mascot_mode=mode,
            mascot_directive=mascot.generation_directive(
                mode,
                category=intake_record.category,
                misunderstanding=intake_record.misunderstanding,
                scenario=scenario,
                support_level=support_level,
                focus_goal=focus_goal,
            ),
            mascot_fallback_line=fallback_line,
            answer_reveal_allowed=mascot.is_answer_reveal(mode, support_level),
            profanity=intake_record.category is IntakeCategory.PROFANITY,
            off_topic=intake_record.category is IntakeCategory.OFF_TOPIC,
            high_concern=intake_record.category is IntakeCategory.HIGH_CONCERN,
            character_bind=intake_record.character_bind,
            misunderstanding=intake_record.misunderstanding,
            pii=intake_record.category is IntakeCategory.PII,
            goal_hint_enabled=character_hints,
        ),
    )

    # ---------------------------------------------------------------- STAGE 4
    record.mascot_line = generation.mascot_line
    if record.mascot_line:
        session.transcript.append(
            Utterance(
                speaker=Speaker.MASCOT, text=record.mascot_line, turn_index=session.turn_index
            )
        )

    if isinstance(scan_result, Exception):
        record.degraded_stages.append("goal_scan")
        record.goal_scan = GoalScanRecord(scanned_goal_ids=sorted(open_goal_ids))
        claims: list[ScanClaim] = []
    else:
        claims = scan_result
        record.goal_scan = goals_module.evaluate_claims(
            claims,
            open_goal_ids=open_goal_ids,
            child_lines=session.child_lines(),
            recent_agent_lines=(
                session.lines_by(Speaker.CHARACTER, goals_module.PARROT_LOOKBACK_TURNS)
                + session.lines_by(Speaker.MASCOT, goals_module.PARROT_LOOKBACK_TURNS)
            ),
        )

    answer_was_revealed = mascot.is_answer_reveal(mode, support_level)
    # [snippet:confirm-goals]
    for goal_id in record.goal_scan.accepted:
        state = session.goals[goal_id]
        state.status = GoalObservationStatus.ACHIEVED
        state.achieved_at_turn = session.turn_index
        state.evidence_turn_index = session.turn_index
        state.achieved_with_support = answer_was_revealed
        if match := next((c for c in claims if c.goal_id == goal_id), None):
            state.evidence_text = match.evidence
            state.why_this_satisfies = match.why_this_satisfies

    # 초점 목표가 이번 턴에 안 끝났으면 도움 세기를 한 칸 올린다.
    if focus_state and focus_state.goal_id not in record.goal_scan.accepted:
        updated, reason = support.tick_support(
            focus_state,
            intake_record.goal_progress,
            scenario.maximum_support_turns_per_micro_goal,
        )
        session.goals[updated.goal_id] = updated
        # 사유까지 남긴다 — "S0 에서 S1 로 왜 올라갔나"를 나중에 못 되짚으면 지표가 반쪽이다.
        if reason is not None:
            record.support_changes.append(
                SupportLevelChange(
                    goal_id=updated.goal_id,
                    from_level=focus_state.support_level,
                    to_level=updated.support_level,
                    reason=reason,
                )
            )
    # [/snippet:confirm-goals]

    session.transcript.append(
        Utterance(speaker=Speaker.CHARACTER, text=generation.text, turn_index=session.turn_index)
    )
    record.delivered_text = generation.text
    record.fallback_used = generation.fallback_used
    record.candidates = generation.candidates

    if end_reason := support.evaluate_end(session, scenario):
        session.phase = SessionPhase.ENDED
        # status 는 세션 생성 때 IN_PROGRESS 로 한 번 쓰이고 아무도 갱신하지 않았다.
        # 그 결과 끝난 세션 42개가 전부 "진행 중"으로 남아 있었다 — 이걸 믿고 집계하면
        # 전부 틀린다. 안전 중단도 성공은 아니지만 enum 에 그 값이 없고, 성공 여부는
        # end_reason 이 들고 있으므로 여기서는 "더 이상 돌지 않는다"만 표시한다.
        session.status = SessionStatus.COMPLETED
        session.end_reason = end_reason
        record.end_reason = end_reason
        farewell = MASCOT_FAREWELL[end_reason]
        session.transcript.append(
            Utterance(speaker=Speaker.MASCOT, text=farewell, turn_index=session.turn_index)
        )
        record.closing_line = farewell
    record.phase_after = session.phase

    record.timings = recorder.timings
    record.total_ms = recorder.total_ms()
    return record
