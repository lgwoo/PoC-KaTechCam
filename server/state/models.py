"""도메인 모델과 Enum.

Enum 값은 팀 ERD(「API 명세서 초안_v3」 §17.1 Enum 사전)의 문자열을 대소문자까지 그대로
쓴다. PoC 전용으로 새로 만든 것은 주석에 명시했다 — 나중에 팀 스키마로 합칠 때 무엇이
합의된 값이고 무엇이 우리가 지어낸 값인지 구분되어야 한다.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# 팀 ERD 합의 Enum
# --------------------------------------------------------------------------


class SupportLevel(StrEnum):
    S0 = "S0"  # 선택지 없는 개방형 질문
    S1 = "S1"  # 상황·표정·몸짓 단서를 포함한 질문
    S2 = "S2"  # 두 개의 선택지 또는 문장 시작점
    S3 = "S3"  # 정답 제공


class GoalObservationStatus(StrEnum):
    NOT_OBSERVED = "NOT_OBSERVED"
    PARTIAL = "PARTIAL"
    ACHIEVED = "ACHIEVED"
    # PoC 확장: S3까지 갔는데도 아이가 스스로 도달하지 못해 그 목표를 접고 다음으로 넘어간 경우.
    # 팀 Enum에는 없다 — 끝까지 도와준 경우를 ACHIEVED로 세면 성공률이 부풀려지므로 분리했다.
    ABANDONED = "ABANDONED"


class SessionStatus(StrEnum):
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


class SafetyStatus(StrEnum):
    SAFE = "SAFE"
    MASKED = "MASKED"
    UNSAFE_BLOCKED = "UNSAFE_BLOCKED"
    ESCALATED = "ESCALATED"


class IncidentSource(StrEnum):
    LEARNER_INPUT = "LEARNER_INPUT"
    AI_OUTPUT = "AI_OUTPUT"


class IncidentType(StrEnum):
    PROFANITY = "PROFANITY"
    AGGRESSION = "AGGRESSION"
    PII_EXPOSURE = "PII_EXPOSURE"
    SELF_HARM_SIGNAL = "SELF_HARM_SIGNAL"
    HARM_TO_OTHERS = "HARM_TO_OTHERS"
    AGE_INAPPROPRIATE = "AGE_INAPPROPRIATE"


class IncidentAction(StrEnum):
    SAFE_REPHRASE = "SAFE_REPHRASE"
    SESSION_PAUSED = "SESSION_PAUSED"
    ESCALATED_TO_INSTRUCTOR = "ESCALATED_TO_INSTRUCTOR"
    ESCALATED_TO_OPERATOR = "ESCALATED_TO_OPERATOR"
    FIXED_SAFE_MESSAGE = "FIXED_SAFE_MESSAGE"


class SupportChangeReason(StrEnum):
    REPEATED_PARTIAL = "REPEATED_PARTIAL"
    HELP_REQUESTED = "HELP_REQUESTED"
    CANNOT_FORM_ANSWER = "CANNOT_FORM_ANSWER"
    MISSED_CUE_REPEATEDLY = "MISSED_CUE_REPEATEDLY"
    CONSECUTIVE_ACHIEVEMENT = "CONSECUTIVE_ACHIEVEMENT"
    SELF_EXPLAINED = "SELF_EXPLAINED"
    APPLIED_WITHOUT_CHOICES = "APPLIED_WITHOUT_CHOICES"


class InputType(StrEnum):
    VOICE = "VOICE"
    CHAT = "CHAT"
    CHOICE = "CHOICE"


# --------------------------------------------------------------------------
# PoC 전용 Enum
# --------------------------------------------------------------------------


class IntakeCategory(StrEnum):
    """인테이크 분류. HIGH_CONCERN은 LLM이 고르지 않는다 —
    Moderation severity가 HIGH일 때 코드가 합성하는 값이다."""

    NORMAL = "NORMAL"
    PROFANITY = "PROFANITY"
    PII = "PII"
    RISK = "RISK"
    OFF_TOPIC = "OFF_TOPIC"
    HIGH_CONCERN = "HIGH_CONCERN"


class ModerationSeverity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    NONE = "NONE"


class MascotMode(StrEnum):
    HALT = "HALT"  # 역할극 중단, 발화를 transcript에서 제외
    CORRECT = "CORRECT"  # 짧게 제지·환기, 캐릭터 응답은 이어감
    SHIELD = "SHIELD"  # 캐릭터가 답하기 곤란 — 마스코트가 대신 받아줌
    SCAFFOLD = "SCAFFOLD"  # 도움 세기 S1~S3 — 마스코트가 명시적으로 유도
    NONE = "NONE"


class SessionPhase(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSING = "CLOSING"
    ENDED = "ENDED"


class EndReason(StrEnum):
    ALL_GOALS = "ALL_GOALS"
    TURN_CAP = "TURN_CAP"
    LADDER_EXHAUSTED = "LADDER_EXHAUSTED"
    SAFETY_HALT = "SAFETY_HALT"


class Speaker(StrEnum):
    CHILD = "CHILD"
    CHARACTER = "CHARACTER"
    MASCOT = "MASCOT"


class GoalProgress(StrEnum):
    TOWARD = "TOWARD"
    NEUTRAL = "NEUTRAL"
    AWAY = "AWAY"


class RefereeViolation(StrEnum):
    ANSWER_LEAK = "ANSWER_LEAK"
    REPETITION = "REPETITION"
    JUDGE_INCOHERENT = "JUDGE_INCOHERENT"
    NO_STEER = "NO_STEER"  # 권고 전용 — 마지막 시도를 막지 않는다


BLOCKING_VIOLATIONS = frozenset(
    {
        RefereeViolation.ANSWER_LEAK,
        RefereeViolation.REPETITION,
        RefereeViolation.JUDGE_INCOHERENT,
    }
)


# --------------------------------------------------------------------------
# 시나리오 자산 (강사 승인 대상 — 역할극 중 변경 불가)
# --------------------------------------------------------------------------


class MicroGoal(BaseModel):
    id: str
    description: str
    required_evidence: list[str]


class Persona(BaseModel):
    character_id: str
    name: str
    personality: str
    speech_style: str
    first_person_rule: str


class Scenario(BaseModel):
    scenario_id: str
    scenario_version: int
    title: str
    scenario_level: str
    scenario_facts: list[str]
    prohibited_inferences: list[str]
    micro_goals: list[MicroGoal]
    persona: Persona
    mascot_intro: str
    character_opening_line: str
    # 지원 수준별 유도 문구. chat.ts에서는 죽은 fixture였다 — 이제 도움 세기 조정이 실제로 쓴다.
    opening_prompts: dict[SupportLevel, str]
    maximum_support_turns_per_micro_goal: int = 4
    maximum_turn_count: int = 6

    def goal(self, goal_id: str) -> MicroGoal | None:
        return next((g for g in self.micro_goals if g.id == goal_id), None)


# --------------------------------------------------------------------------
# 세션 상태
# --------------------------------------------------------------------------


class Utterance(BaseModel):
    speaker: Speaker
    text: str
    turn_index: int


class GoalState(BaseModel):
    goal_id: str
    status: GoalObservationStatus = GoalObservationStatus.NOT_OBSERVED
    support_level: SupportLevel = SupportLevel.S0
    support_turns_used: int = 0
    achieved_at_turn: int | None = None
    evidence_text: str | None = None
    evidence_turn_index: int | None = None
    why_this_satisfies: str | None = None
    # S3에서 정답을 공개한 뒤 달성된 경우. 문서 §14의 achieved_without_answer_reveal의 반대값.
    achieved_with_support: bool = False

    @property
    def is_open(self) -> bool:
        """아직 유도를 계속해야 하는 목표인가."""
        return self.status in (
            GoalObservationStatus.NOT_OBSERVED,
            GoalObservationStatus.PARTIAL,
        )


class SessionState(BaseModel):
    session_id: str
    scenario_id: str
    phase: SessionPhase = SessionPhase.ACTIVE
    status: SessionStatus = SessionStatus.IN_PROGRESS
    turn_index: int = 0
    transcript: list[Utterance] = Field(default_factory=list)
    goals: dict[str, GoalState] = Field(default_factory=dict)
    focus_goal_id: str | None = None
    end_reason: EndReason | None = None

    def open_goals(self) -> list[GoalState]:
        return [g for g in self.goals.values() if g.is_open]

    def child_lines(self) -> list[str]:
        return [u.text for u in self.transcript if u.speaker is Speaker.CHILD]

    def lines_by(self, speaker: Speaker, last_n_turns: int) -> list[str]:
        cutoff = self.turn_index - last_n_turns
        return [u.text for u in self.transcript if u.speaker is speaker and u.turn_index > cutoff]


# --------------------------------------------------------------------------
# 턴 기록 (관측 콘솔이 그대로 렌더링한다)
# --------------------------------------------------------------------------


class StageTiming(BaseModel):
    """duration만 저장하면 안 된다 — 병렬 구간이 있어 합계가 체감 시간과 다르다.
    턴 시작 기준 오프셋이 있어야 콘솔에서 간트로 그릴 수 있다."""

    stage: str
    start_offset_ms: int
    duration_ms: int
    ok: bool = True
    attempt_no: int | None = None


class ModerationOutcome(BaseModel):
    available: bool
    flagged: bool = False
    categories: list[str] = Field(default_factory=list)
    category_scores: dict[str, float] = Field(default_factory=dict)
    severity: ModerationSeverity = ModerationSeverity.NONE


class JudgeOutcome(BaseModel):
    safe_to_send: bool
    decision: str
    failure_codes: list[str] = Field(default_factory=list)
    reason: str = ""
    revision_change: list[str] = Field(default_factory=list)
    revision_avoid: list[str] = Field(default_factory=list)


class CandidateRecord(BaseModel):
    attempt_no: int
    text: str
    mascot_text: str | None = None  # 같은 콜에서 나온 마스코트 대사 후보
    judge: JudgeOutcome | None = None
    moderation: ModerationOutcome | None = None
    referee_violations: list[RefereeViolation] = Field(default_factory=list)
    delivered: bool = False
    outcome: str = ""  # PASS / REGENERATE / SAFETY_REGENERATE / REFEREE_BLOCKED / ERROR


class RejectedEvidence(BaseModel):
    goal_id: str
    evidence: str
    reason: str


class GoalScanRecord(BaseModel):
    scanned_goal_ids: list[str] = Field(default_factory=list)
    accepted: list[str] = Field(default_factory=list)
    rejected: list[RejectedEvidence] = Field(default_factory=list)


class IntakeRecord(BaseModel):
    category: IntakeCategory
    reason: str
    misunderstanding: bool = False
    character_bind: bool = False
    goal_progress: GoalProgress = GoalProgress.NEUTRAL
    masked_text: str | None = None
    override_source: str | None = None  # severity 오버라이드가 걸렸으면 그 사유
    degraded: bool = False


class HintSource(StrEnum):
    """이번 턴에 학습 목표 힌트를 누가 줬는가.

    마스코트만 세면 절반을 놓친다 — S0 에서는 캐릭터 프롬프트에 목표 힌트가 들어가서
    캐릭터가 대사 속에 은근히 유도한다. 그 턴도 아이 입장에서는 도움을 받은 턴이다.
    """

    MASCOT = "MASCOT"  # 마스코트가 대놓고 유도 질문을 했다
    CHARACTER = "CHARACTER"  # 캐릭터 프롬프트에 목표 힌트가 들어갔다
    NONE = "NONE"  # 남은 목표가 없거나 마지막 턴이라 유도하지 않았다


class SupportLevelChange(BaseModel):
    goal_id: str
    from_level: SupportLevel
    to_level: SupportLevel
    reason: SupportChangeReason


class TurnRecord(BaseModel):
    session_id: str
    turn_index: int
    child_input: str
    child_input_stored: str  # 마스킹 후 transcript에 실제로 들어간 값
    intake: IntakeRecord
    moderation: ModerationOutcome
    mascot_mode: MascotMode
    mascot_line: str | None = None  # 턴 중 개입 대사
    closing_line: str | None = None  # 세션 마무리 인사 — 개입과 성격이 달라 분리한다
    focus_goal_id: str | None = None
    support_level: SupportLevel = SupportLevel.S0
    hint_source: HintSource = HintSource.NONE
    # 이 턴에 도움 세기가 올라갔다면 그 내역. 안 올랐으면 빈 리스트.
    support_changes: list[SupportLevelChange] = Field(default_factory=list)
    candidates: list[CandidateRecord] = Field(default_factory=list)
    delivered_text: str | None = None
    fallback_used: bool = False
    goal_scan: GoalScanRecord = Field(default_factory=GoalScanRecord)
    timings: list[StageTiming] = Field(default_factory=list)
    degraded_stages: list[str] = Field(default_factory=list)
    total_ms: int = 0
    phase_after: SessionPhase = SessionPhase.ACTIVE
    end_reason: EndReason | None = None
