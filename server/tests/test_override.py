"""Moderation severity 티어와 방어적 오버라이드 테스트."""

from __future__ import annotations

from safety.override import apply_severity_override, normalize_categories, severity_of
from state.models import IntakeCategory, ModerationSeverity


def test_normalizes_sdk_field_names_to_api_names():
    """OpenAI Python SDK 는 self-harm/intent 를 self_harm_intent 로 준다.
    정규화가 없으면 CRITICAL 판정이 조용히 새어 나간다."""
    assert "self-harm" in normalize_categories(["self_harm"])
    assert "self-harm/intent" in normalize_categories(["self_harm_intent"])
    assert "sexual/minors" in normalize_categories(["sexual_minors"])
    assert "violence/graphic" in normalize_categories(["violence_graphic"])


def test_severity_tiers():
    assert severity_of(["self_harm"]) is ModerationSeverity.CRITICAL
    assert severity_of(["sexual/minors"]) is ModerationSeverity.CRITICAL
    assert severity_of(["violence_graphic"]) is ModerationSeverity.HIGH
    assert severity_of(["sexual"]) is ModerationSeverity.HIGH
    assert severity_of(["violence"]) is ModerationSeverity.MODERATE
    assert severity_of([]) is ModerationSeverity.NONE


def test_critical_forces_risk_over_llm_verdict():
    """자체 분류기가 NORMAL 이라 해도 Moderation 이 CRITICAL 이면 RISK 다."""
    category, reason = apply_severity_override(
        IntakeCategory.NORMAL, ModerationSeverity.CRITICAL, ["self_harm"]
    )
    assert category is IntakeCategory.RISK
    assert reason is not None and "self-harm" in reason


def test_high_escalates_to_high_concern():
    category, reason = apply_severity_override(
        IntakeCategory.PROFANITY, ModerationSeverity.HIGH, ["violence_graphic"]
    )
    assert category is IntakeCategory.HIGH_CONCERN
    assert reason is not None


def test_high_does_not_downgrade_risk():
    """이미 RISK 인데 HIGH 라고 HIGH_CONCERN 으로 내리면 방어망이 무너진다."""
    category, reason = apply_severity_override(
        IntakeCategory.RISK, ModerationSeverity.HIGH, ["violence_graphic"]
    )
    assert category is IntakeCategory.RISK
    assert reason is None


def test_quiet_moderation_leaves_llm_verdict_alone():
    category, reason = apply_severity_override(
        IntakeCategory.PROFANITY, ModerationSeverity.NONE, []
    )
    assert category is IntakeCategory.PROFANITY
    assert reason is None


def test_moderate_does_not_escalate():
    """MODERATE 는 순화·코칭만 하고 역할극을 멈추지 않는다."""
    category, reason = apply_severity_override(
        IntakeCategory.NORMAL, ModerationSeverity.MODERATE, ["violence"]
    )
    assert category is IntakeCategory.NORMAL
    assert reason is None
