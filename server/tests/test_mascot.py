"""마스코트 모드 결정 — 안전이 유도를 항상 이긴다.

여기가 뚫리면 위험 발화에 대고 학습 목표를 유도하는 일이 생긴다. 우선순위 사다리는
위에서부터 먼저 걸리는 게 이기는 구조라서(pipeline/mascot.py select_mode),
아래 항목이 위 항목을 덮지 않는다는 것을 각각 확인해야 한다.
"""

from __future__ import annotations

import pytest

from pipeline import mascot
from state.models import (
    GoalProgress,
    IntakeCategory,
    MascotMode,
    ModerationSeverity,
    SupportLevel,
)
from tests.conftest import TEST_SCENARIO

NORMAL = IntakeCategory.NORMAL
NONE_SEV = ModerationSeverity.NONE


def mode(**overrides: object) -> MascotMode:
    """유도가 걸리는 상태를 기본값으로 둔다 — 안전 조건이 그것을 덮는지 보려면
    덮을 대상이 있어야 한다."""
    kwargs: dict = {
        "category": NORMAL,
        "severity": NONE_SEV,
        "misunderstanding": False,
        "character_bind": False,
        "support_level": SupportLevel.S1,
        "has_open_goal": True,
        "goal_progress": GoalProgress.NEUTRAL,
    }
    kwargs.update(overrides)
    return mascot.select_mode(**kwargs)


# ---------------------------------------------------------------- HALT


def test_risk_category_halts() -> None:
    assert mode(category=IntakeCategory.RISK) is MascotMode.HALT


def test_moderation_critical_halts_even_when_intake_said_normal() -> None:
    """자체 분류가 놓쳐도 Moderation CRITICAL 하나로 멈춰야 한다.
    두 트리거가 독립이어야 판정 하나가 틀렸을 때 방어망이 남는다."""
    assert mode(severity=ModerationSeverity.CRITICAL) is MascotMode.HALT


def test_halt_beats_correct_and_scaffold() -> None:
    assert (
        mode(
            category=IntakeCategory.RISK,
            misunderstanding=True,
            character_bind=True,
            support_level=SupportLevel.S3,
        )
        is MascotMode.HALT
    )


# -------------------------------------------------------------- CORRECT


@pytest.mark.parametrize(
    "category",
    [IntakeCategory.PROFANITY, IntakeCategory.OFF_TOPIC, IntakeCategory.HIGH_CONCERN],
)
def test_correction_categories(category: IntakeCategory) -> None:
    assert mode(category=category) is MascotMode.CORRECT


def test_misunderstanding_corrects() -> None:
    assert mode(misunderstanding=True) is MascotMode.CORRECT


def test_correct_beats_shield_and_scaffold() -> None:
    """제지를 건너뛰고 대신 받아주거나 유도하면, 아이는 자기 말이 문제였다는 걸 모른다."""
    assert (
        mode(
            category=IntakeCategory.PROFANITY,
            character_bind=True,
            support_level=SupportLevel.S3,
        )
        is MascotMode.CORRECT
    )


# --------------------------------------------------------------- SHIELD


def test_character_bind_shields() -> None:
    assert mode(character_bind=True) is MascotMode.SHIELD


def test_shield_beats_scaffold() -> None:
    assert (
        mode(character_bind=True, support_level=SupportLevel.S3) is MascotMode.SHIELD
    )


# ------------------------------------------------------------- SCAFFOLD
#
# 세 조건이 모두 맞아야 유도한다. 하나씩 무너뜨려서 각각이 실제로 필요한지 본다.


def test_scaffold_needs_all_three() -> None:
    assert mode() is MascotMode.SCAFFOLD


def test_no_open_goal_means_no_scaffold() -> None:
    assert mode(has_open_goal=False) is MascotMode.NONE


def test_s0_means_no_scaffold() -> None:
    """도움 세기가 아직 S0 이면 아이에게 먼저 기회를 준다."""
    assert mode(support_level=SupportLevel.S0) is MascotMode.NONE


def test_progressing_child_is_not_scaffolded() -> None:
    """아이가 목표로 가고 있는데 같은 유도 문구를 또 들이대면 방해가 된다."""
    assert mode(goal_progress=GoalProgress.TOWARD) is MascotMode.NONE


def test_pii_alone_does_not_wake_the_mascot() -> None:
    """개인정보는 마스킹으로 처리한다 — 마스코트가 나서서 아이를 나무랄 일이 아니다."""
    assert mode(category=IntakeCategory.PII, support_level=SupportLevel.S0) is MascotMode.NONE
    assert mode(category=IntakeCategory.PII) is MascotMode.SCAFFOLD


# ------------------------------------------------------ 사전 승인 대사


def line(mode_value: MascotMode, **overrides: object) -> str | None:
    kwargs: dict = {
        "category": NORMAL,
        "misunderstanding": False,
        "scenario": TEST_SCENARIO,
        "support_level": SupportLevel.S1,
        "focus_goal": TEST_SCENARIO.micro_goals[0],
    }
    kwargs.update(overrides)
    return mascot.fallback_line(mode_value, **kwargs)


def test_halt_line_is_the_risk_line() -> None:
    """중단은 생성을 아예 건너뛴다. 그래서 이 대사는 LLM 이 아니라 여기서 나온다."""
    assert line(MascotMode.HALT) == mascot.FALLBACK_LINES["RISK"]


@pytest.mark.parametrize(
    "category",
    [IntakeCategory.PROFANITY, IntakeCategory.OFF_TOPIC, IntakeCategory.HIGH_CONCERN],
)
def test_correct_line_matches_category(category: IntakeCategory) -> None:
    assert line(MascotMode.CORRECT, category=category) == mascot.FALLBACK_LINES[category.value]


def test_correct_line_falls_back_to_misunderstanding() -> None:
    assert (
        line(MascotMode.CORRECT, misunderstanding=True)
        == mascot.FALLBACK_LINES["MISUNDERSTANDING"]
    )


def test_shield_line() -> None:
    assert line(MascotMode.SHIELD) == mascot.FALLBACK_LINES["SHIELD"]


def test_scaffold_line_comes_from_the_scenario() -> None:
    for level in (SupportLevel.S0, SupportLevel.S1, SupportLevel.S2, SupportLevel.S3):
        assert line(MascotMode.SCAFFOLD, support_level=level) == (
            TEST_SCENARIO.opening_prompts[level]
        )


def test_scaffold_line_is_none_when_the_scenario_lacks_the_level() -> None:
    """시나리오에 그 수준 문구가 없으면 None 이 나온다. 후보가 3회 다 타면 이 None 이
    그대로 마스코트 대사 자리에 들어가므로, 조용히 빈 대사가 나갈 수 있다."""
    thin = TEST_SCENARIO.model_copy(deep=True)
    thin.opening_prompts.pop(SupportLevel.S2)
    assert line(MascotMode.SCAFFOLD, scenario=thin, support_level=SupportLevel.S2) is None


def test_none_mode_has_no_line() -> None:
    assert line(MascotMode.NONE) is None


# ------------------------------------------------------------ 생성 지시


def directive(mode_value: MascotMode, **overrides: object) -> str:
    kwargs: dict = {
        "category": NORMAL,
        "misunderstanding": False,
        "scenario": TEST_SCENARIO,
        "support_level": SupportLevel.S1,
        "focus_goal": TEST_SCENARIO.micro_goals[0],
    }
    kwargs.update(overrides)
    return mascot.generation_directive(mode_value, **kwargs)


def test_none_mode_tells_the_model_to_leave_the_line_null() -> None:
    assert "null" in directive(MascotMode.NONE)


def test_correct_directive_forbids_steering_instead_of_stopping() -> None:
    text = directive(MascotMode.CORRECT, category=IntakeCategory.PROFANITY)
    assert "제지" in text


def test_scaffold_directive_names_the_goal() -> None:
    text = directive(MascotMode.SCAFFOLD)
    assert TEST_SCENARIO.micro_goals[0].description in text


def test_directive_carries_the_reference_line_but_forbids_copying() -> None:
    text = directive(MascotMode.SHIELD)
    assert mascot.FALLBACK_LINES["SHIELD"] in text


# --------------------------------------------------------- 정답 공개 여부


def test_answer_reveal_only_at_s3_scaffold() -> None:
    """정답 공개는 지표에 영향을 준다 — achieved_with_support 가 이 값으로 정해진다."""
    assert mascot.is_answer_reveal(MascotMode.SCAFFOLD, SupportLevel.S3)
    assert not mascot.is_answer_reveal(MascotMode.SCAFFOLD, SupportLevel.S2)
    assert not mascot.is_answer_reveal(MascotMode.NONE, SupportLevel.S3)
    assert not mascot.is_answer_reveal(MascotMode.CORRECT, SupportLevel.S3)


def test_character_hints_everywhere_except_scaffold() -> None:
    """마스코트가 유도를 맡은 턴에는 캐릭터가 질문을 겹치지 않는다."""
    assert not mascot.character_gets_goal_hint(MascotMode.SCAFFOLD)
    for other in (MascotMode.NONE, MascotMode.CORRECT, MascotMode.SHIELD, MascotMode.HALT):
        assert mascot.character_gets_goal_hint(other)
