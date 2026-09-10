"""마스코트 개입 모드 선택. 순수 함수.

TS 판에서는 마스코트가 안전 카테고리 4종에만 반응하는 고정 문구 3개였다. 여기서는
상황 오인(misunderstanding)과 캐릭터 곤란(character_bind)까지 받고, 도움 세기가 S1 이상이면
목표 유도까지 마스코트가 맡는다.

**핵심 설계:** S0 에서는 캐릭터가 은근히 유도하고(생성 프롬프트의 goal hint),
S1~S3 에서는 마스코트가 명시적으로 유도하며 캐릭터 프롬프트에서 목표 힌트를 뺀다.
TS 판은 도움 세기를 캐릭터 프롬프트 안에 숨겼는데, 이렇게 하면 겉으로 드러나서
콘솔에서 "지금 누가 왜 유도하는지"가 보인다.

우선순위: HALT > CORRECT > SHIELD > SCAFFOLD > NONE.
안전이 유도를 이긴다 — 욕설과 목표 유도가 동시에 걸리면 이번 턴은 제지만 하고
유도는 다음 턴으로 미룬다.
"""

from __future__ import annotations

from state.models import (
    GoalProgress,
    IntakeCategory,
    MascotMode,
    MicroGoal,
    ModerationSeverity,
    Scenario,
    SupportLevel,
)

# chat.ts:31-35 에서 옮긴 고정 문구. LLM 이 마스코트 대사를 못 만들었거나
# 그 대사가 안전 검사를 통과하지 못했을 때 쓰는 안전망이다.
FALLBACK_LINES: dict[str, str] = {
    "RISK": "잠깐, 지금 이야기는 선생님이나 어른한테 꼭 알려야 할 것 같아. 잠시 여기서 멈출게.",
    "PROFANITY": "(마스코트가 옆에서 살짝 끼어든다) 얘들아, 그런 말은 마음을 아프게 할 수 있어~",
    "OFF_TOPIC": "(마스코트가 톡톡 건드린다) 잠깐! 지금은 다른 이야기 중이었지? 다시 돌아가 볼까?",
    "HIGH_CONCERN": (
        "(마스코트가 앞으로 나선다) 잠깐, 그런 표현은 안 돼. 서로 다치게 하는 말은 하지 말자, 알겠지?"
    ),
    "MISUNDERSTANDING": (
        "(마스코트가 고개를 갸웃한다) 음, 잠깐만. 아까 무슨 일이 있었는지 다시 한번 볼까?"
    ),
    "SHIELD": "(마스코트가 슬쩍 앞으로 나선다) 그건 내가 대신 답해 줄게!",
}


def select_mode(
    *,
    category: IntakeCategory,
    severity: ModerationSeverity,
    misunderstanding: bool,
    character_bind: bool,
    support_level: SupportLevel,
    has_open_goal: bool,
    goal_progress: GoalProgress = GoalProgress.NEUTRAL,
) -> MascotMode:
    # [snippet:mascot-mode]
    if category is IntakeCategory.RISK or severity is ModerationSeverity.CRITICAL:
        return MascotMode.HALT

    if category in (
        IntakeCategory.PROFANITY,
        IntakeCategory.OFF_TOPIC,
        IntakeCategory.HIGH_CONCERN,
    ):
        return MascotMode.CORRECT

    if misunderstanding:
        return MascotMode.CORRECT

    if character_bind:
        return MascotMode.SHIELD

    # 도움 세기가 올라가 있어도 아이가 목표 쪽으로 오고 있으면 끼어들지 않는다.
    # 실측에서 이 조건이 없으니 잘하고 있는 아이에게 매 턴 같은 유도 문구를 반복했다 —
    # 지원 수준이 안 내려가는 동안 opening_prompts[S1] 이 계속 나온다.
    if (
        has_open_goal
        and support_level is not SupportLevel.S0
        and goal_progress is not GoalProgress.TOWARD
    ):
        return MascotMode.SCAFFOLD

    return MascotMode.NONE
    # [/snippet:mascot-mode]


def character_replies(mode: MascotMode) -> bool:
    """HALT 에서는 캐릭터가 말하지 않는다 — 보호 절차가 역할극보다 우선한다."""
    return mode is not MascotMode.HALT


def excludes_from_transcript(mode: MascotMode) -> bool:
    """SAFE-06: 안전 사건은 일반 학습 기록과 분리한다.
    HALT 를 부른 발화는 transcript 에 남기지 않는다."""
    return mode is MascotMode.HALT


def character_gets_goal_hint(mode: MascotMode) -> bool:
    """S1 이상에서는 마스코트가 유도하므로 캐릭터 프롬프트에 목표 힌트를 넣지 않는다.
    둘 다 유도하면 한 턴에 질문이 두 개가 되어 아이가 답을 못 만든다."""
    return mode is not MascotMode.SCAFFOLD


def fallback_line(
    mode: MascotMode,
    *,
    category: IntakeCategory,
    misunderstanding: bool,
    scenario: Scenario,
    support_level: SupportLevel,
    focus_goal: MicroGoal | None,
) -> str | None:
    """LLM 이 마스코트 대사를 못 줬을 때 쓸 사전 검토된 문구."""
    if mode is MascotMode.HALT:
        return FALLBACK_LINES["RISK"]

    if mode is MascotMode.CORRECT:
        if category in (
            IntakeCategory.PROFANITY,
            IntakeCategory.OFF_TOPIC,
            IntakeCategory.HIGH_CONCERN,
        ):
            return FALLBACK_LINES[category.value]
        if misunderstanding:
            return FALLBACK_LINES["MISUNDERSTANDING"]
        return None

    if mode is MascotMode.SHIELD:
        return FALLBACK_LINES["SHIELD"]

    if mode is MascotMode.SCAFFOLD:
        # 시나리오의 지원 수준별 유도 문구. TS 판에서 죽어 있던 openingPrompts 를
        # 여기서 되살린다 — 3인칭 설명 톤이라 캐릭터에게는 못 쓰지만 마스코트에게는 맞다.
        return scenario.opening_prompts.get(support_level)

    return None


# 제지 임무 설명. 무엇을 짚어야 하는지 못 박지 않으면 모델이 제지 대신 목표 유도를
# 해버린다 — 실측에서 그랬고, 그래서 한동안 고정 문구만 썼다.
_CORRECTION_DUTY = {
    IntakeCategory.PROFANITY: (
        "아이가 방금 욕설이나 남을 깎아내리는 말을 했다. 마스코트는 **그 말 자체를 짚어** "
        "그런 말은 마음을 아프게 한다는 취지로 짧게 제지해야 한다."
    ),
    IntakeCategory.OFF_TOPIC: (
        "아이가 지금 상황과 상관없는 이야기를 꺼냈다. 마스코트는 그 점을 짚고 원래 하던 "
        "이야기로 돌아가자고 짧게 환기해야 한다."
    ),
    IntakeCategory.HIGH_CONCERN: (
        "아이가 남을 다치게 하는 표현을 했다. 마스코트는 그 표현을 되풀이하지 말고, "
        "그런 말은 하면 안 된다고 분명하게 제지해야 한다."
    ),
}

_MISUNDERSTANDING_DUTY = (
    "아이가 무슨 일이 있었는지 잘못 알고 말했다. 마스코트는 틀렸다고 지적하지 말고 "
    "무슨 일이 있었는지 다시 같이 보자는 쪽으로 짧게 되돌려야 한다."
)

_SHIELD_DUTY = (
    "캐릭터가 1인칭으로는 답하기 곤란한 것을 아이가 물었다. 마스코트가 대신 받아줘야 한다. "
    "정답을 알려주는 게 아니라 그 질문을 마스코트가 떠안는 것이다."
)


def generation_directive(
    mode: MascotMode,
    *,
    category: IntakeCategory,
    misunderstanding: bool,
    scenario: Scenario,
    support_level: SupportLevel,
    focus_goal: MicroGoal | None,
) -> str:
    """이번 턴 마스코트가 무슨 일을 해야 하는지 생성 프롬프트에 실을 지시문.

    참고 문구를 같이 준다. 그대로 베끼라는 게 아니라 수위와 취지를 보이려는 것이다 —
    특히 SCAFFOLD 는 지원 수준별로 정답을 어디까지 흘려도 되는지가 이미 설계돼 있다.
    """
    if mode is MascotMode.NONE:
        return "이번 턴에는 마스코트가 나서지 않는다. mascot_line 을 null 로 둬라."

    duty: str
    if mode is MascotMode.CORRECT:
        duty = _CORRECTION_DUTY.get(category) or _MISUNDERSTANDING_DUTY
        if misunderstanding and category not in _CORRECTION_DUTY:
            duty = _MISUNDERSTANDING_DUTY
        duty += (
            " 제지를 건너뛰고 학습 목표를 유도하면 안 된다 — 이번 턴 마스코트의 임무는 제지다."
        )
    elif mode is MascotMode.SHIELD:
        duty = _SHIELD_DUTY
    elif mode is MascotMode.SCAFFOLD:
        goal_text = f'지금 유도 중인 목표: "{focus_goal.description}"\n' if focus_goal else ""
        duty = (
            f"{goal_text}마스코트가 이번 턴의 질문을 맡는다. 아래 참고 문구와 **같은 수위로** "
            "아이에게 물어라. 참고 문구보다 더 많이 알려주면 안 된다."
        )
    else:  # HALT 는 생성 콜을 타지 않는다.
        duty = "마스코트가 짧게 개입한다."

    reference = fallback_line(
        mode,
        category=category,
        misunderstanding=misunderstanding,
        scenario=scenario,
        support_level=support_level,
        focus_goal=focus_goal,
    )
    if not reference:
        return duty
    return (
        f"{duty}\n[참고 문구] “{reference}”\n"
        "이 문구를 그대로 베끼지 마라. 수위와 취지만 참고해서 지금 대화에 맞는 네 말로 써라."
    )


def is_answer_reveal(mode: MascotMode, support_level: SupportLevel) -> bool:
    """이 턴에 정답이 공개되는가. 이후 목표 달성은 achieved_with_support 로 표시된다."""
    return mode is MascotMode.SCAFFOLD and support_level is SupportLevel.S3
