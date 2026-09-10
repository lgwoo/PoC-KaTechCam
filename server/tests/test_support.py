"""S0~S3 도움 세기와 세션 종료 판정 테스트."""

from __future__ import annotations

import pytest

from pipeline.support import answer_revealed, evaluate_end, pick_focus_goal, tick_support
from state.models import (
    EndReason,
    GoalObservationStatus,
    GoalProgress,
    GoalState,
    MicroGoal,
    Persona,
    Scenario,
    SessionState,
    SupportChangeReason,
    SupportLevel,
)

# 도움 세기 조정은 시나리오 내용이 아니라 목표 개수와 상한만 본다. 테스트가 자기 시나리오를 들고 있어야
# 실제 시나리오 문구가 바뀌어도 이 테스트가 흔들리지 않는다.
FALL_SCENARIO = Scenario(
    scenario_id="SCN-TEST",
    scenario_version=1,
    title="테스트 시나리오",
    scenario_level="L1",
    scenario_facts=["친구가 넘어졌다."],
    prohibited_inferences=["감정을 단정하지 않는다."],
    micro_goals=[
        MicroGoal(id=f"MG-0{i}", description=f"목표 {i}", required_evidence=[f"근거 {i}"])
        for i in (1, 2, 3)
    ],
    persona=Persona(
        character_id="TEST",
        name="테스트 친구",
        personality="소심함",
        speech_style="반말",
        first_person_rule="1인칭으로만 말한다.",
    ),
    mascot_intro="멍멍!",
    character_opening_line="...아야...",
    opening_prompts={
        SupportLevel.S0: "어떤 기분일까?",
        SupportLevel.S1: "넘어져서 울고 있어. 어떤 기분일까?",
        SupportLevel.S2: "기쁠까, 속상할까?",
        SupportLevel.S3: "넘어져서 속상한 기분이야.",
    },
)

MAX_TURNS = FALL_SCENARIO.maximum_support_turns_per_micro_goal  # 4


def fresh_session(**goal_overrides: GoalObservationStatus) -> SessionState:
    goals = {}
    for micro_goal in FALL_SCENARIO.micro_goals:
        goals[micro_goal.id] = GoalState(
            goal_id=micro_goal.id,
            status=goal_overrides.get(micro_goal.id, GoalObservationStatus.NOT_OBSERVED),
        )
    return SessionState(
        session_id="S1", scenario_id=FALL_SCENARIO.scenario_id, goals=goals
    )


def test_focus_follows_scenario_order():
    session = fresh_session(**{"MG-01": GoalObservationStatus.ACHIEVED})
    focus = pick_focus_goal(session, FALL_SCENARIO)
    assert focus is not None and focus.goal_id == "MG-02"


def test_focus_is_none_when_all_closed():
    session = fresh_session(
        **{
            "MG-01": GoalObservationStatus.ACHIEVED,
            "MG-02": GoalObservationStatus.ACHIEVED,
            "MG-03": GoalObservationStatus.ABANDONED,
        }
    )
    assert pick_focus_goal(session, FALL_SCENARIO) is None


def test_escalates_one_step_when_child_is_stuck():
    goal = GoalState(goal_id="MG-02")
    updated, reason = tick_support(goal, GoalProgress.NEUTRAL, MAX_TURNS)
    assert updated.support_level is SupportLevel.S1
    assert updated.support_turns_used == 1
    assert reason is SupportChangeReason.REPEATED_PARTIAL


def test_progress_holds_the_level():
    """목표 쪽으로 오고 있으면 지원을 더 얹지 않는다 —
    올려버리면 스스로 도달할 기회를 뺏고 S3 정답 유출을 앞당긴다."""
    goal = GoalState(goal_id="MG-02")
    updated, reason = tick_support(goal, GoalProgress.TOWARD, MAX_TURNS)
    assert updated.support_level is SupportLevel.S0
    assert updated.status is GoalObservationStatus.PARTIAL
    assert reason is None


def test_moving_away_uses_missed_cue_reason():
    goal = GoalState(goal_id="MG-02")
    _, reason = tick_support(goal, GoalProgress.AWAY, MAX_TURNS)
    assert reason is SupportChangeReason.MISSED_CUE_REPEATEDLY


def test_help_request_reason():
    goal = GoalState(goal_id="MG-02")
    _, reason = tick_support(goal, GoalProgress.NEUTRAL, MAX_TURNS, help_requested=True)
    assert reason is SupportChangeReason.HELP_REQUESTED


def test_walks_s0_to_s3_then_abandons():
    goal = GoalState(goal_id="MG-02")
    levels = []
    for _ in range(MAX_TURNS):
        goal, _ = tick_support(goal, GoalProgress.NEUTRAL, MAX_TURNS)
        levels.append(goal.support_level)

    assert levels[:3] == [SupportLevel.S1, SupportLevel.S2, SupportLevel.S3]
    # 4턴을 다 쓰면 이 목표는 접는다. ACHIEVED 로 세면 성공률이 부풀려진다.
    assert goal.status is GoalObservationStatus.ABANDONED
    assert not goal.is_open


def test_answer_revealed_only_at_s3():
    assert not answer_revealed(GoalState(goal_id="MG-02", support_level=SupportLevel.S2))
    assert answer_revealed(GoalState(goal_id="MG-02", support_level=SupportLevel.S3))


def test_turn_cap_beats_everything():
    """픽스처가 서로 모순된다 — maximum_turn_count=6 인데 목표 3개 × 4턴 = 12.
    턴 상한이 이긴다."""
    session = fresh_session()
    session.turn_index = FALL_SCENARIO.maximum_turn_count
    assert evaluate_end(session, FALL_SCENARIO) is EndReason.TURN_CAP


def test_no_end_while_goals_are_open():
    session = fresh_session()
    session.turn_index = 1
    assert evaluate_end(session, FALL_SCENARIO) is None


def test_all_goals_achieved_ends_normally():
    session = fresh_session(
        **{g.id: GoalObservationStatus.ACHIEVED for g in FALL_SCENARIO.micro_goals}
    )
    session.turn_index = 3
    assert evaluate_end(session, FALL_SCENARIO) is EndReason.ALL_GOALS


def test_all_abandoned_is_not_reported_as_success():
    session = fresh_session(
        **{g.id: GoalObservationStatus.ABANDONED for g in FALL_SCENARIO.micro_goals}
    )
    session.turn_index = 3
    assert evaluate_end(session, FALL_SCENARIO) is EndReason.LADDER_EXHAUSTED


@pytest.mark.parametrize("achieved_count", [1, 2])
def test_partial_success_still_counts_as_all_goals(achieved_count: int):
    """하나라도 스스로 도달했으면 정상 완료로 본다. 나머지는 기록에 ABANDONED 로 남는다."""
    statuses = {}
    for index, micro_goal in enumerate(FALL_SCENARIO.micro_goals):
        statuses[micro_goal.id] = (
            GoalObservationStatus.ACHIEVED
            if index < achieved_count
            else GoalObservationStatus.ABANDONED
        )
    session = fresh_session(**statuses)
    session.turn_index = 4
    assert evaluate_end(session, FALL_SCENARIO) is EndReason.ALL_GOALS
