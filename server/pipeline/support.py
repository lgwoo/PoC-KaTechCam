"""S0~S3 도움 세기 조정과 세션 종료 판정. 전부 순수 함수다.

근거: 워크플로우 문서 §14. TS 판(chat.ts)에는 이 로직이 아예 없었다 —
openingPrompts.S0~S3 와 maximumSupportTurnsPerMicroGoal 이 fixture 로만 있고
읽는 코드가 없었다. 여기서 실제로 동작하게 만든다.

PoC 단순화: 각 목표는 S0 에서 시작한다. 문서상 초기 지원 수준은 표정 퀴즈 기반
difficulty_decision 에서 오는데 그 단계는 이번 범위 밖이다.
"""

from __future__ import annotations

from state.models import (
    EndReason,
    GoalObservationStatus,
    GoalProgress,
    GoalState,
    Scenario,
    SessionState,
    SupportChangeReason,
    SupportLevel,
)

_ESCALATION = {
    SupportLevel.S0: SupportLevel.S1,
    SupportLevel.S1: SupportLevel.S2,
    SupportLevel.S2: SupportLevel.S3,
    SupportLevel.S3: SupportLevel.S3,  # 천장
}


def pick_focus_goal(session: SessionState, scenario: Scenario) -> GoalState | None:
    """시나리오 정의 순서대로 아직 열려 있는 첫 목표."""
    for micro_goal in scenario.micro_goals:
        state = session.goals.get(micro_goal.id)
        if state and state.is_open:
            return state
    return None


def tick_support(
    goal: GoalState,
    progress: GoalProgress,
    max_support_turns: int,
    help_requested: bool = False,
) -> tuple[GoalState, SupportChangeReason | None]:
    """초점 목표에서 한 턴을 쓴 뒤의 도움 세기.

    이 목표가 이번 턴에 달성됐다면 호출하지 않는다 — 달성 처리가 먼저다.
    기술 오류(STT·네트워크·API)로 인한 턴에도 호출하면 안 된다(문서 §14 "조정하지 않는 경우").

    Returns: (갱신된 목표 상태, 지원 수준을 올렸다면 그 사유)
    """
    updated = goal.model_copy(deep=True)
    updated.support_turns_used += 1

    # 유도 한도 소진 — 이 목표는 접고 다음으로 넘어간다.
    # ACHIEVED 로 세면 성공률이 부풀려지므로 ABANDONED 로 분리한다.
    if updated.support_turns_used >= max_support_turns:
        updated.status = GoalObservationStatus.ABANDONED
        return updated, None

    # 아이가 목표 쪽으로 오고 있으면 지원을 더 얹지 않는다.
    # 여기서 올려버리면 스스로 도달할 기회를 뺏고 S3 정답 유출을 앞당긴다.
    if progress is GoalProgress.TOWARD:
        updated.status = GoalObservationStatus.PARTIAL
        return updated, None

    next_level = _ESCALATION[updated.support_level]
    if next_level == updated.support_level:
        return updated, None

    updated.support_level = next_level
    if help_requested:
        reason = SupportChangeReason.HELP_REQUESTED
    elif progress is GoalProgress.AWAY:
        reason = SupportChangeReason.MISSED_CUE_REPEATEDLY
    else:
        reason = SupportChangeReason.REPEATED_PARTIAL
    return updated, reason


def answer_revealed(goal: GoalState) -> bool:
    """S3 는 정답을 그대로 알려주는 단계다. 이 턴 이후의 목표 달성은
    아이가 스스로 도달한 것으로 셀 수 없다."""
    return goal.support_level is SupportLevel.S3


def evaluate_end(session: SessionState, scenario: Scenario) -> EndReason | None:
    """세션을 마무리로 보낼 사유가 생겼는가. 없으면 None.

    턴 상한이 도움 세기보다 우선한다 — 픽스처가 서로 모순되기 때문이다
    (maximum_turn_count=6 인데 목표 3개 × 목표당 4턴 = 12).
    """
    if session.turn_index >= scenario.maximum_turn_count:
        return EndReason.TURN_CAP

    if any(goal.is_open for goal in session.goals.values()):
        return None

    # 모든 목표가 닫혔다. 하나라도 스스로 도달했으면 정상 완료,
    # 끝까지 도와줬는데도 못 도달한 경우면 그렇게 기록한다.
    if any(g.status is GoalObservationStatus.ACHIEVED for g in session.goals.values()):
        return EndReason.ALL_GOALS
    return EndReason.LADDER_EXHAUSTED
