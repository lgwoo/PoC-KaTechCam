"""주제 한 줄로 역할극 시나리오를 짓는다.

강사 승인 전 초안이다. 아이에게 그대로 노출되는 문장(마스코트 인트로·캐릭터 첫 대사·
유도 문구)이 섞여 있으므로 대사와 같은 안전 검사를 통과해야 한다.

모델이 정하지 않는 값이 있다 — 시나리오 ID, 버전, 목표 번호(MG-01…), 1인칭 규칙.
번호와 규칙은 파이프라인 다른 곳이 문자열로 의존하므로 코드가 붙인다.
"""

from __future__ import annotations

import uuid

from llm.client import LlmClient, StageRecorder
from llm.contracts import ScenarioDraftContract
from pipeline import prompts
from safety.moderation import ModerationClient
from state.models import (
    MicroGoal,
    ModerationSeverity,
    Persona,
    Scenario,
    SupportLevel,
)

_FIRST_PERSON_RULE = (
    "캐릭터는 항상 1인칭으로 말한다. "
    "상황을 설명하는 3인칭 나레이션(예: '친구가 울고 있어')을 하지 않는다."
)

_ALLOWED_LEVELS = ("L1", "L2", "L3")

# 아이에게 노출되는 문장은 대사와 같은 문턱을 쓴다.
_BLOCKING_SEVERITY = frozenset({ModerationSeverity.HIGH, ModerationSeverity.CRITICAL})


class ScenarioDraftError(Exception):
    """초안이 쓸 수 없는 상태다. 호출부가 사용자에게 사유를 보여준다."""


def _clean(values: list[str]) -> list[str]:
    return [v.strip() for v in values if v and v.strip()]


def _to_scenario(draft: ScenarioDraftContract, theme: str) -> Scenario:
    goals = _clean([g.description for g in draft.micro_goals])
    if len(goals) != 3:
        raise ScenarioDraftError(f"마이크로 목표가 3개여야 하는데 {len(goals)}개가 나왔다.")

    facts = _clean(draft.scenario_facts)
    if not facts:
        raise ScenarioDraftError("일어난 일이 비어 있다.")

    level = draft.scenario_level.strip().upper()
    if level not in _ALLOWED_LEVELS:
        level = "L2"

    micro_goals = [
        MicroGoal(
            id=f"MG-{index + 1:02d}",
            description=goal.description.strip(),
            required_evidence=_clean(goal.required_evidence) or [goal.description.strip()],
        )
        for index, goal in enumerate(draft.micro_goals)
    ]

    return Scenario(
        scenario_id=f"SCN-GEN-{uuid.uuid4().hex[:8].upper()}",
        scenario_version=1,
        title=draft.title.strip() or theme.strip(),
        scenario_level=level,
        scenario_facts=facts,
        prohibited_inferences=_clean(draft.prohibited_inferences)
        or ["아이가 말하기 전에 캐릭터가 감정을 단정하지 않는다."],
        micro_goals=micro_goals,
        persona=Persona(
            character_id="GENERATED",
            name=draft.persona.name.strip() or draft.title.strip(),
            personality=draft.persona.personality.strip(),
            speech_style=draft.persona.speech_style.strip(),
            first_person_rule=_FIRST_PERSON_RULE,
        ),
        mascot_intro=draft.mascot_intro.strip(),
        character_opening_line=draft.character_opening_line.strip(),
        opening_prompts={
            SupportLevel.S0: draft.opening_prompt_s0.strip(),
            SupportLevel.S1: draft.opening_prompt_s1.strip(),
            SupportLevel.S2: draft.opening_prompt_s2.strip(),
            SupportLevel.S3: draft.opening_prompt_s3.strip(),
        },
    )


def _child_facing_text(scenario: Scenario) -> str:
    """아이가 실제로 읽거나 듣게 되는 문장만 모은다."""
    return "\n".join(
        [
            scenario.title,
            scenario.mascot_intro,
            scenario.character_opening_line,
            *scenario.opening_prompts.values(),
            *(goal.description for goal in scenario.micro_goals),
        ]
    )


async def draft_scenario(
    *,
    client: LlmClient,
    moderation: ModerationClient,
    recorder: StageRecorder,
    theme: str,
    level: str,
) -> Scenario:
    draft = await client.structured(
        stage="scenario_author",
        recorder=recorder,
        system=prompts.scenario_author_system(),
        user=prompts.scenario_author_user(theme, level),
        contract=ScenarioDraftContract,
    )
    scenario = _to_scenario(draft, theme)

    outcome = await moderation.check(
        _child_facing_text(scenario), stage="moderation.scenario", recorder=recorder
    )
    if outcome.severity in _BLOCKING_SEVERITY:
        raise ScenarioDraftError(
            f"생성된 시나리오가 안전 검사에 걸렸다({', '.join(outcome.categories) or '사유 미상'}). "
            "다른 주제로 다시 시도하라."
        )
    return scenario
