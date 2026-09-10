"""Moderation severity 티어와 방어적 오버라이드.

poc/src/providers/moderation.ts 의 티어 정의와 chat.ts:278-288 의 오버라이드 로직을
그대로 옮겼다. 핵심은 defense-in-depth 다 — LLM 분류기와 Moderation 이 서로 독립적이라
한쪽이 놓쳐도 다른 쪽이 잡는다. **Moderation 이 더 심각하다고 보면 언제나 그쪽이 이긴다.**

주의: OpenAI Python SDK 의 `categories.model_dump()` 는 필드명을 `self_harm` 처럼
언더스코어로 준다. 반면 API 원문과 아래 티어 정의는 `self-harm` 처럼 하이픈이다.
normalize_categories() 로 반드시 통일한 뒤 판정한다 — 안 그러면 CRITICAL 이 조용히 샌다.
"""

from __future__ import annotations

from state.models import IntakeCategory, ModerationSeverity

# 자해·아동 성적 — 자체 분류가 뭐라 했든 즉시 RISK 로 강제한다.
CRITICAL_CATEGORIES = frozenset(
    {"self-harm", "self-harm/intent", "self-harm/instructions", "sexual/minors"}
)

# 구체적 위협·그래픽 폭력 — 역할극을 멈추진 않되 마스코트가 확실히 개입한다.
HIGH_CATEGORIES = frozenset(
    {
        "harassment/threatening",
        "hate/threatening",
        "illicit/violent",
        "violence/graphic",
        "sexual",  # 아동 대상 서비스라 일반 MODERATE 보다 격상
    }
)


def normalize_categories(categories: list[str]) -> list[str]:
    """`self_harm_intent` → `self-harm/intent`. SDK 필드명을 API 표기로 되돌린다."""
    normalized: list[str] = []
    for raw in categories:
        name = raw.replace("_", "-")
        # SDK 는 슬래시를 언더스코어로 바꾼다: self-harm/intent → self_harm_intent
        for critical in CRITICAL_CATEGORIES | HIGH_CATEGORIES:
            if name == critical.replace("/", "-"):
                name = critical
                break
        normalized.append(name)
    return normalized


def severity_of(categories: list[str]) -> ModerationSeverity:
    normalized = set(normalize_categories(categories))
    if normalized & CRITICAL_CATEGORIES:
        return ModerationSeverity.CRITICAL
    if normalized & HIGH_CATEGORIES:
        return ModerationSeverity.HIGH
    if normalized:
        return ModerationSeverity.MODERATE
    return ModerationSeverity.NONE


def apply_severity_override(
    category: IntakeCategory,
    severity: ModerationSeverity,
    flagged_categories: list[str],
) -> tuple[IntakeCategory, str | None]:
    """LLM 분류 위에 Moderation 판정을 덮어씌운다.

    Returns: (최종 카테고리, 오버라이드 사유 또는 None)

    등급을 **낮추지는 않는다.** 이미 RISK 인데 Moderation 이 조용하다고 NORMAL 로
    내리면 방어망이 무너진다.
    """
    hits = ", ".join(normalize_categories(flagged_categories))

    if severity is ModerationSeverity.CRITICAL and category is not IntakeCategory.RISK:
        return IntakeCategory.RISK, (
            f"Moderation CRITICAL({hits}) — 자체 분류({category})보다 우선하여 RISK로 강제"
        )

    if severity is ModerationSeverity.HIGH and category not in (
        IntakeCategory.RISK,
        IntakeCategory.HIGH_CONCERN,
    ):
        return IntakeCategory.HIGH_CONCERN, (
            f"Moderation HIGH({hits}) — 자체 분류({category})보다 우선하여 HIGH_CONCERN으로 격상"
        )

    return category, None
