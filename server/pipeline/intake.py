"""STAGE 1 — 입력 안전 분류 + 마스코트 신호. LLM 1콜과 Moderation 을 병렬로 돈다.

두 판정은 서로의 결과를 필요로 하지 않으므로 동시에 부른다. 그리고 **Moderation 이
더 심각하다고 보면 언제나 그쪽이 이긴다**(safety/override.py) — 한쪽이 놓쳐도 다른
쪽이 잡는 이중 안전망이다.

인테이크 LLM 이 구조를 어기면 NORMAL + degraded 로 넘어가되, Moderation severity 가
HIGH 이상이면 그 판정을 그대로 살려 RISK/HIGH_CONCERN 으로 간다. 분류기가 죽었다고
안전망까지 같이 죽으면 안 된다.
"""

from __future__ import annotations

import asyncio

from llm.client import LlmClient, StageRecorder
from llm.contracts import IntakeContract
from llm.errors import LlmCallError, LlmContractError
from pipeline import prompts
from safety.moderation import ModerationClient
from safety.override import apply_severity_override
from state.models import (
    GoalProgress,
    IntakeCategory,
    IntakeRecord,
    MicroGoal,
    ModerationOutcome,
    ModerationSeverity,
    Scenario,
)


async def run_intake(
    *,
    client: LlmClient,
    moderation: ModerationClient,
    recorder: StageRecorder,
    scenario: Scenario,
    recent_history: str,
    focus_goal: MicroGoal | None,
    utterance: str,
) -> tuple[IntakeRecord, ModerationOutcome]:
    moderation_task = moderation.check(utterance, stage="moderation.input", recorder=recorder)
    intake_task = client.structured(
        stage="intake",
        recorder=recorder,
        system=prompts.intake_system(scenario, recent_history, focus_goal),
        user=prompts.intake_user(utterance),
        contract=IntakeContract,
    )
    moderation_result, intake_result = await asyncio.gather(
        moderation_task, intake_task, return_exceptions=True
    )

    moderation_outcome = (
        moderation_result
        if isinstance(moderation_result, ModerationOutcome)
        else ModerationOutcome(available=False)
    )

    if isinstance(intake_result, IntakeContract):
        parsed = intake_result
        degraded = False
        reason = parsed.reason
    elif isinstance(intake_result, (LlmCallError, LlmContractError)):
        parsed = IntakeContract(category="NORMAL", reason="")
        degraded = True
        reason = f"인테이크 판정 실패 — NORMAL 로 처리하고 Moderation 판정에 의존한다 ({intake_result})"
    else:
        parsed = IntakeContract(category="NORMAL", reason="")
        degraded = True
        reason = f"인테이크 판정 실패: {intake_result!r}"

    # 계약은 문자열 Literal 이다(HIGH_CONCERN 은 스키마에서 뺐다 — 코드가 합성하는 값이라서).
    category = IntakeCategory(parsed.category)

    category, override_reason = apply_severity_override(
        category, moderation_outcome.severity, moderation_outcome.categories
    )

    # 분류기가 죽은 상태에서 Moderation 도 조용하면 그냥 NORMAL 로 간다.
    # 반대로 Moderation 이 HIGH 이상이면 위 오버라이드가 이미 등급을 올려놨다.
    if degraded and moderation_outcome.severity is ModerationSeverity.NONE:
        reason += " (Moderation 도 이상 없음)"

    record = IntakeRecord(
        category=category,
        reason=override_reason or reason,
        misunderstanding=parsed.misunderstanding,
        character_bind=parsed.character_bind,
        goal_progress=parsed.goal_progress if not degraded else GoalProgress.NEUTRAL,
        masked_text=(parsed.masked_text or None) if category is IntakeCategory.PII else None,
        override_source=override_reason,
        degraded=degraded,
    )
    return record, moderation_outcome
