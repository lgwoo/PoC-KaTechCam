export type Speaker = "CHILD" | "CHARACTER" | "MASCOT";
export type MascotMode = "HALT" | "CORRECT" | "SHIELD" | "SCAFFOLD" | "NONE";
export type SupportLevel = "S0" | "S1" | "S2" | "S3";
export type GoalStatus = "NOT_OBSERVED" | "PARTIAL" | "ACHIEVED" | "ABANDONED";

export interface Utterance {
  speaker: Speaker;
  text: string;
  turn_index: number;
}

export interface MicroGoal {
  id: string;
  description: string;
  required_evidence: string[];
}

export interface GoalState {
  goal_id: string;
  status: GoalStatus;
  support_level: SupportLevel;
  support_turns_used: number;
  achieved_at_turn: number | null;
  evidence_text: string | null;
  why_this_satisfies: string | null;
  achieved_with_support: boolean;
}

export interface StageTiming {
  stage: string;
  start_offset_ms: number;
  duration_ms: number;
  ok: boolean;
  attempt_no: number | null;
}

export interface ModerationOutcome {
  available: boolean;
  flagged: boolean;
  categories: string[];
  category_scores: Record<string, number>;
  severity: "CRITICAL" | "HIGH" | "MODERATE" | "NONE";
}

export interface JudgeOutcome {
  safe_to_send: boolean;
  decision: string;
  failure_codes: string[];
  reason: string;
  revision_change: string[];
  revision_avoid: string[];
}

export interface CandidateRecord {
  attempt_no: number;
  text: string;
  mascot_text: string | null;
  judge: JudgeOutcome | null;
  moderation: ModerationOutcome | null;
  referee_violations: string[];
  delivered: boolean;
  outcome: string;
}

export interface IntakeRecord {
  category: string;
  reason: string;
  misunderstanding: boolean;
  character_bind: boolean;
  goal_progress: "TOWARD" | "NEUTRAL" | "AWAY";
  masked_text: string | null;
  mascot_line: string | null;
  override_source: string | null;
  degraded: boolean;
}

export interface RejectedEvidence {
  goal_id: string;
  evidence: string;
  reason: string;
}

export interface TurnRecord {
  session_id: string;
  turn_index: number;
  child_input: string;
  child_input_stored: string;
  intake: IntakeRecord;
  moderation: ModerationOutcome;
  mascot_mode: MascotMode;
  mascot_line: string | null;
  closing_line: string | null;
  focus_goal_id: string | null;
  support_level: SupportLevel;
  hint_source: "MASCOT" | "CHARACTER" | "NONE";
  support_changes: {
    goal_id: string;
    from_level: SupportLevel;
    to_level: SupportLevel;
    reason: string;
  }[];
  candidates: CandidateRecord[];
  delivered_text: string | null;
  fallback_used: boolean;
  goal_scan: {
    scanned_goal_ids: string[];
    accepted: string[];
    rejected: RejectedEvidence[];
  };
  timings: StageTiming[];
  degraded_stages: string[];
  total_ms: number;
  phase_after: string;
  end_reason: string | null;
}

export interface ScenarioView {
  scenario_id: string;
  title: string;
  scenario_level: string;
  scenario_facts: string[];
  mascot_intro: string;
  character_opening_line: string;
  character_name: string;
  maximum_turn_count: number;
  max_support_turns: number;
  micro_goals: MicroGoal[];
  prohibited_inferences: string[];
  generation: GenerationMetrics | null;
}

export interface GenerationMetrics {
  total_ms: number;
  timings: StageTiming[];
}

export interface ScenarioOption extends ScenarioView {
  key: string;
  generated?: boolean;
}

export interface ModelInfo {
  agent: string;
  moderation: string | null;
}

export interface SessionView {
  models: ModelInfo;
  session_id: string;
  phase: string;
  turn_index: number;
  end_reason: string | null;
  scenario: ScenarioView;
  transcript: Utterance[];
  goals: GoalState[];
  summary: {
    total: number;
    achieved: number;
    achieved_without_answer_reveal: number;
    abandoned: number;
  };
  hints: HintSummary;
}

export interface SupportEscalation {
  subgoal_id: string;
  from_level: string;
  to_level: string;
  reason_code: string;
  turn_number: number;
}

export interface HintSummary {
  turns: number;
  hinted_turns: number;
  from_mascot: number;
  from_character: number;
  unhinted_turns: number;
  escalations: SupportEscalation[];
  answer_revealed: number;
}

// 지난 역할극 열람 — 서버가 재시작돼도 DB 에 남은 기록을 읽는다.
// GET /api/sessions 는 ROLEPLAY_SESSION 행을 그대로 준다.
export interface SessionListRow {
  session_id: string;
  scenario_id: string;
  status: string;
  phase: string;
  turn_index: number;
  end_reason: string | null;
  started_at: string;
  ended_at: string | null;
}

// GET /api/sessions/{id} 의 turns 는 TurnRecord 가 아니라 DB 행을 되읽은 모양이다.
// 필드 이름이 다르므로 같은 타입으로 묶지 않는다 — 묶으면 어느 쪽이 원본인지 흐려진다.
export interface StoredCandidate {
  attempt_no: number;
  text: string;
  outcome: string;
  delivered: number;
  judge_decision: string | null;
  judge_reason: string | null;
  judge_failure_codes: string | null;
}

export interface StoredTurn {
  turn_id: string;
  turn_number: number;
  child_input: string;
  child_input_stored: string;
  safety_status: string;
  focus_subgoal_id: string | null;
  support_level: SupportLevel;
  mascot_mode: MascotMode;
  mascot_line: string | null;
  closing_line: string | null;
  delivered_text: string | null;
  fallback_used: boolean;
  degraded_stages: string[];
  total_ms: number;
  end_reason: string | null;
  intake: { category: string; reason: string; override_source: string | null } | null;
  candidates: StoredCandidate[];
  timings: StageTiming[];
  goal_rejections: { subgoal_id: string; evidence: string; reason: string }[];
}

export interface StoredGoalStatus {
  subgoal_id: string;
  status: GoalStatus;
  reached_level: SupportLevel;
  evidence_text: string | null;
  achieved_with_support: number;
}

// live=false 는 DB 에서 되살린 세션이라는 뜻이다 — 새 턴을 이어 갈 수 없다.
export interface StoredSessionView extends SessionView {
  live: boolean;
  turns: StoredTurn[];
  goal_statuses: StoredGoalStatus[];
}
