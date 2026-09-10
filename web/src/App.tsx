import { useCallback, useEffect, useRef, useState } from "react";
import {
  createSession,
  deleteScenario,
  generateScenario,
  listScenarios,
  postTurn,
} from "./api";
import { CODE_SNIPPETS } from "./snippets";
import type {
  CandidateRecord,
  ScenarioOption,
  GoalState,
  HintSummary,
  MicroGoal,
  ModelInfo,
  SessionView,
  StageTiming,
  TurnRecord,
} from "./types";
import ArchiveView from "./ArchiveView";
import {
  END_REASON_LABEL,
  GOAL_STATUS_LABEL,
  INTAKE_CATEGORY_LABEL,
  MASCOT_MODE_LABEL,
  SUPPORT_LEVEL_LABEL,
} from "./labels";

const GOAL_PROGRESS_LABEL: Record<string, string> = {
  TOWARD: "목표에 가까워짐",
  NEUTRAL: "제자리",
  AWAY: "목표에서 멀어짐",
};

const SEVERITY_LABEL: Record<string, string> = {
  CRITICAL: "심각",
  HIGH: "높음",
  MODERATE: "보통",
  NONE: "없음",
};

const REFEREE_VIOLATION_LABEL: Record<string, string> = {
  ANSWER_LEAK: "정답 유출",
  REPETITION: "같은 말 반복",
  JUDGE_INCOHERENT: "판정 불일치",
  NO_STEER: "유도 없음",
};

const OUTCOME_LABEL: Record<string, string> = {
  PASS: "통과",
  REGENERATE: "재생성",
  SAFETY_REGENERATE: "안전 재생성",
  JUDGE_ERROR: "심판 실패",
  REFEREE_BLOCKED: "심판 차단",
  ERROR: "생성 실패",
  EMPTY: "빈 응답",
};

// 단계 → 이 단계에서 무슨 역할이 도는가 + 어느 엔진을 부르는가.
// engine 은 세션의 models 로 실제 모델명을 채운다 — .env 가 바뀌면 표시도 따라간다.
const STAGE_META: Record<string, { label: string; role: string; engine: "agent" | "moderation" }> =
  {
    "moderation.input": { label: "입력 검열", role: "안전 필터", engine: "moderation" },
    intake: { label: "발화 판정", role: "입력 판단자", engine: "agent" },
    goal_scan: { label: "목표 스캔", role: "원인 판단 에이전트", engine: "agent" },
    generate: { label: "대사 생성", role: "마스코트+캐릭터 생성", engine: "agent" },
    "moderation.mascot": { label: "마스코트 검열", role: "안전 필터", engine: "moderation" },
    "moderation.output": { label: "출력 검열", role: "안전 필터", engine: "moderation" },
    judge: { label: "판정", role: "응답 판단 에이전트", engine: "agent" },
  };

// OpenAI Moderation 이 돌려주는 카테고리 표기 그대로가 키다.
const MODERATION_CATEGORY_LABEL: Record<string, string> = {
  harassment: "괴롭힘",
  "harassment/threatening": "괴롭힘·협박",
  hate: "혐오",
  "hate/threatening": "혐오·협박",
  "self-harm": "자해",
  "self-harm/intent": "자해 의도",
  "self-harm/instructions": "자해 방법",
  sexual: "성적 내용",
  "sexual/minors": "아동 성적 내용",
  violence: "폭력",
  "violence/graphic": "잔인한 폭력",
  illicit: "불법 행위",
  "illicit/violent": "폭력적 불법 행위",
};

function stageLabel(stage: string, models?: ModelInfo): string {
  const suffix = ".repair";
  const isRepair = stage.endsWith(suffix);
  const base = isRepair ? stage.slice(0, -suffix.length) : stage;
  const meta = STAGE_META[base];
  if (!meta) return isRepair ? `${base} 재요청` : base;

  const name = isRepair ? `${meta.label} 재요청` : meta.label;
  if (!models) return name;
  const model =
    meta.engine === "moderation" ? (models.moderation ?? "미설정") : models.agent;
  return `${name}(${meta.role} / ${model})`;
}

// ---------------------------------------------------------------- 시퀀스 다이어그램

type Lane = { key: string; title: string; sub: string; kind: "actor" | "code" | "mod" | "llm" };

type SeqMsg =
  | { type: "msg"; from: number; to: number; label: string; sub?: string; emphasis?: boolean }
  | { type: "self"; at: number; label: string; sub?: string }
  | { type: "note"; label: string }
  | { type: "outcome"; tone: "ok" | "warn" | "danger"; label: string; sub?: string };

type SeqGroup = {
  kind: "par" | "loop" | "alt";
  label: string;
  from: number;
  to: number;
  depth: number;
  // alt 전용 — 이 인덱스 행 위에 갈래를 가르는 점선을 긋는다.
  divider?: number;
};

const SEQ_LANE_X = [80, 285, 520, 760];
const SEQ_HEAD_H = 54;
const SEQ_TOP = 74;
const SEQ_ROW = 44;
const SEQ_WIDTH = 860;

function buildSequence(models: ModelInfo) {
  const agent = models.agent;
  const mod = models.moderation ?? "미설정";

  const lanes: Lane[] = [
    { key: "child", title: "아이 / 콘솔", sub: "브라우저", kind: "actor" },
    { key: "server", title: "서버 코드", sub: "LLM 아님 · pipeline/turn.py", kind: "code" },
    { key: "mod", title: "안전 필터", sub: mod, kind: "mod" },
    { key: "agent", title: "에이전트", sub: agent, kind: "llm" },
  ];

  const messages: SeqMsg[] = [
    { type: "msg", from: 0, to: 1, label: "아이 발화 전송", sub: "턴 하나가 여기서 시작된다" },
    { type: "note", label: "STAGE 1 — 입력 판정" },
    { type: "msg", from: 1, to: 2, label: "입력 검열 요청", sub: "검사 대상: 아이 발화" },
    {
      type: "msg",
      from: 1,
      to: 3,
      label: "발화 판정 요청",
      sub: "방금 한 말 한 문장만 — 안전한가 · 개인정보가 있나 · 상황을 잘못 알았나",
    },
    {
      type: "msg",
      from: 2,
      to: 1,
      label: "심각도",
      sub: "없음 / 보통 / 높음 / 심각 — 걸린 항목도 함께",
    },
    {
      type: "msg",
      from: 3,
      to: 1,
      label: "분류 결과 + 상황 신호",
      sub: "일반·욕설·개인정보·위험·주제이탈 / 상황 오인 / 목표에 가까워졌나",
    },
    { type: "note", label: "STAGE 2 — 코드 판단 (LLM 호출 없음)" },
    {
      type: "self",
      at: 1,
      label: "마스코트 모드 결정",
      sub: "더 심각하게 본 쪽을 채택 → 제지·환기 / 대신 받아줌 / 목표 유도 / 개입 없음",
    },
    { type: "note", label: "STAGE 3 — 스캔과 생성" },
    {
      type: "msg",
      from: 1,
      to: 3,
      label: "목표 스캔 요청",
      sub: "대화 전체에서 목표 달성 근거를 찾는다",
    },
    {
      type: "msg",
      from: 1,
      to: 3,
      label: "대사 생성 요청 — 단 1회",
      sub: "마스코트 지시 + 캐릭터 페르소나를 한 프롬프트에",
      emphasis: true,
    },
    {
      type: "msg",
      from: 3,
      to: 1,
      label: "마스코트 대사 + 캐릭터 대사",
      sub: "응답 하나에 두 대사가 같이 담겨 온다 (필드 mascot_line · character_line)",
      emphasis: true,
    },
    {
      type: "msg",
      from: 1,
      to: 2,
      label: "출력 검열 — 두 대사를 한 번에",
      sub: "캐릭터 대사와 마스코트 대사를 배열로 함께 보낸다",
      emphasis: true,
    },
    { type: "msg", from: 1, to: 3, label: "판정 요청", sub: "두 대사를 함께 심사" },
    {
      type: "msg",
      from: 3,
      to: 1,
      label: "통과 또는 재생성",
      sub: "재생성이면 무엇이 잘못됐는지와 어떻게 고칠지를 함께 받는다",
    },
    {
      type: "self",
      at: 1,
      label: "심판 (순수 함수)",
      sub: "정답 유출 · 반복 · 유도 여부 재검사",
    },
    {
      type: "outcome",
      tone: "ok",
      label: "[통과] 방금 만든 두 대사를 그대로 아이에게 전달한다",
    },
    {
      type: "outcome",
      tone: "warn",
      label: "[걸림] 방금 만든 두 대사를 버리고, 고칠 점을 붙여 처음부터 다시 만든다",
      sub: "검열에 걸렸거나 · 판정이 반려했거나 · 심판이 막았거나 — 걸린 이유가 곧 고칠 점이 된다",
    },
    {
      type: "outcome",
      tone: "danger",
      label: "세 번 다 걸리면 → 만든 대사를 모두 버리고 사전 승인된 고정 문구를 내보낸다",
      sub: "콘솔에 ‘사전 승인된 기본 응답으로 대체됨’으로 표시된다",
    },
    { type: "msg", from: 3, to: 1, label: "목표 스캔 결과", sub: "달성 근거 목록" },
    { type: "note", label: "STAGE 4 — 확정" },
    {
      type: "self",
      at: 1,
      label: "목표 확정 → 도움 세기 조정 → 종료 판정",
      sub: "달성한 목표를 기록하고, 못 했으면 다음 턴에 더 알려주도록 한 칸 올린다",
    },
    {
      type: "msg",
      from: 1,
      to: 0,
      label: "마스코트 대사 + 캐릭터 대사",
      sub: "관측 기록과 함께 반환",
    },
  ];

  // 인덱스는 messages 배열 기준이다 — 위 배열을 고치면 여기도 같이 봐야 한다.
  const groups: SeqGroup[] = [
    { kind: "par", label: "입력 검열 ‖ 발화 판정", from: 2, to: 5, depth: 0 },
    { kind: "par", label: "목표 스캔 ‖ 대사 생성 루프", from: 9, to: 19, depth: 0 },
    { kind: "loop", label: "여기까지가 한 번의 시도 · 최대 3회", from: 10, to: 17, depth: 1 },
    { kind: "par", label: "검열 ‖ 판정", from: 12, to: 14, depth: 2 },
    {
      kind: "alt",
      label: "둘 중 하나로 갈린다",
      from: 16,
      to: 17,
      depth: 2,
      divider: 17,
    },
  ];

  return { lanes, messages, groups };
}

function SequenceDiagram({ models }: { models: ModelInfo }) {
  const { lanes, messages, groups } = buildSequence(models);
  const rowY = (index: number) => SEQ_TOP + SEQ_HEAD_H + index * SEQ_ROW;
  const height = rowY(messages.length) + 20;

  return (
    <div className="seq-wrap">
      <svg viewBox={`0 0 ${SEQ_WIDTH} ${height}`} className="seq" role="img">
        <defs>
          <marker id="arw" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto">
            <path d="M0,0 L8,3 L0,6 z" fill="var(--muted)" />
          </marker>
          <marker id="arw-hot" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
            <path d="M0,0 L8,3 L0,6 z" fill="var(--accent)" />
          </marker>
        </defs>

        {/* 생명선 */}
        {lanes.map((lane, i) => (
          <line
            key={lane.key}
            x1={SEQ_LANE_X[i]}
            y1={SEQ_TOP + SEQ_HEAD_H}
            x2={SEQ_LANE_X[i]}
            y2={height - 12}
            className="seq-life"
          />
        ))}

        {/* 그룹 박스는 화살표 뒤에 깔린다 */}
        {groups.map((group, gi) => {
          const inset = group.depth * 13;
          const last = messages[group.to];
          // 설명줄(sub)은 행 기준선보다 아래에 그려진다 — 박스가 그걸 자르지 않게 더 내린다.
          const tail = last && "sub" in last && last.sub ? 24 : 14;
          const top = rowY(group.from) - 26 + group.depth * 4;
          // 바깥 프레임일수록 아래로 더 내려야 안쪽 프레임을 감싼 게 보인다.
          const bottom = rowY(group.to) + tail + (2 - group.depth) * 5;
          const x = 26 + inset;
          const width = SEQ_WIDTH - 52 - inset * 2;
          return (
            <g key={`${group.kind}-${gi}`} className={`seq-group ${group.kind}`}>
              <rect x={x} y={top} width={width} height={bottom - top} rx="8" />
              <text x={x + 7} y={top + 12}>
                {group.kind === "par" ? "병렬" : group.kind === "loop" ? "루프" : "분기"} ·{" "}
                {group.label}
              </text>
              {group.divider !== undefined && (
                <line
                  className="seq-divider"
                  x1={x}
                  y1={rowY(group.divider) - 20}
                  x2={x + width}
                  y2={rowY(group.divider) - 20}
                />
              )}
            </g>
          );
        })}

        {/* 참여자 헤더 */}
        {lanes.map((lane, i) => (
          <g key={`head-${lane.key}`} className={`seq-head ${lane.kind}`}>
            <rect x={SEQ_LANE_X[i] - 74} y={SEQ_TOP - 6} width="148" height={SEQ_HEAD_H - 8} rx="8" />
            <text x={SEQ_LANE_X[i]} y={SEQ_TOP + 12} className="seq-head-title">
              {lane.title}
            </text>
            <text x={SEQ_LANE_X[i]} y={SEQ_TOP + 28} className="seq-head-sub">
              {lane.sub}
            </text>
          </g>
        ))}

        {/* 메시지 */}
        {messages.map((message, i) => {
          const y = rowY(i);
          if (message.type === "note") {
            return (
              <text key={i} x={30} y={y + 4} className="seq-note">
                {message.label}
              </text>
            );
          }
          if (message.type === "outcome") {
            const x = SEQ_LANE_X[1] - 40;
            return (
              <g key={i} className={`seq-outcome ${message.tone}`}>
                <circle cx={x} cy={y - 4} r="4" />
                <text x={x + 12} y={y - 1}>
                  {message.label}
                </text>
                {message.sub && (
                  <text x={x + 12} y={y + 12} className="seq-sub">
                    {message.sub}
                  </text>
                )}
              </g>
            );
          }
          if (message.type === "self") {
            const x = SEQ_LANE_X[message.at];
            return (
              <g key={i} className="seq-self">
                <path d={`M${x},${y - 10} h30 v18 h-30`} />
                <text x={x + 38} y={y - 2}>
                  {message.label}
                </text>
                {message.sub && (
                  <text x={x + 38} y={y + 11} className="seq-sub">
                    {message.sub}
                  </text>
                )}
              </g>
            );
          }
          const x1 = SEQ_LANE_X[message.from];
          const x2 = SEQ_LANE_X[message.to];
          const back = x2 < x1;
          const mid = (x1 + x2) / 2;
          return (
            <g key={i} className={`seq-msg${message.emphasis ? " hot" : ""}${back ? " back" : ""}`}>
              <line
                x1={x1}
                y1={y}
                x2={x2 + (back ? 7 : -7)}
                y2={y}
                markerEnd={message.emphasis ? "url(#arw-hot)" : "url(#arw)"}
              />
              <text x={mid} y={y - 7}>
                {message.label}
              </text>
              {message.sub && (
                <text x={mid} y={y + 15} className="seq-sub">
                  {message.sub}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function CodeSnippets() {
  return (
    <div className="snippets">
      <h3>코드가 판단하는 자리 — 실제 코드</h3>
      <p className="muted">
        위 다이어그램에서 &lsquo;서버 코드&rsquo; 줄에 붙은 네모(자기 호출)가 하는 일이다. LLM을
        부르지 않고 코드가 결정하므로 같은 입력이면 항상 같은 결과가 나온다.
      </p>
      {CODE_SNIPPETS.map((snippet) => (
        <div className="snippet" key={snippet.title}>
          <div className="snippet-head">
            <strong>{snippet.title}</strong>
            <code>{snippet.file}</code>
          </div>
          <p className="muted">{snippet.note}</p>
          <pre>
            <code>{snippet.code}</code>
          </pre>
        </div>
      ))}
    </div>
  );
}

const SCENARIO_STAGE_LABEL: Record<string, string> = {
  scenario_author: "시나리오 저작",
  "moderation.scenario": "시나리오 안전 검사",
};

function draftStageLabel(stage: string, models: ModelInfo | null): string {
  const suffix = ".repair";
  const isRepair = stage.endsWith(suffix);
  const base = isRepair ? stage.slice(0, -suffix.length) : stage;
  const name = SCENARIO_STAGE_LABEL[base] ?? base;
  const model = base.startsWith("moderation")
    ? (models?.moderation ?? "미설정")
    : (models?.agent ?? "");
  return `${name}${isRepair ? " 재요청" : ""}${model ? `(${model})` : ""}`;
}

function ScenarioDetail({
  scenario,
  models,
  justCreated,
}: {
  scenario: ScenarioOption;
  models: ModelInfo | null;
  justCreated: boolean;
}) {
  const generation = scenario.generation;
  return (
    <div className="draft-result">
      <div className="draft-result-head">
        <strong>{scenario.title}</strong>
        <span className="tag subtle">난이도 {scenario.scenario_level}</span>
        <span className="tag subtle">캐릭터 {scenario.character_name}</span>
        {generation && (
          <span className="tag ok">생성 {(generation.total_ms / 1000).toFixed(1)}초</span>
        )}
      </div>
      <p className="muted">
        {justCreated
          ? "방금 만들었습니다. 세션 시작을 누르면 바로 해볼 수 있어요."
          : "세션 시작을 누르면 이 시나리오로 바로 해볼 수 있어요."}
      </p>

      <div className={generation ? "draft-cols" : "draft-cols single"}>
        {generation && (
          <div>
            <h4>만드는 데 걸린 시간</h4>
            <ul className="draft-list">
              {generation.timings.map((timing, index) => (
                <li key={`${timing.stage}-${index}`}>
                  <span>{draftStageLabel(timing.stage, models)}</span>
                  <b>{timing.duration_ms}ms</b>
                </li>
              ))}
            </ul>
            <p className="hint">만들 때 한 번 재서 저장해 둔 값이다. 역할극 턴 시간과는 별개다.</p>
          </div>
        )}

        <div>
          <h4>일어난 일</h4>
          <ul className="draft-list plain">
            {scenario.scenario_facts.map((fact) => (
              <li key={fact}>{fact}</li>
            ))}
          </ul>
        </div>
      </div>

      <h4>마이크로 목표 — 아이에게는 안 보인다</h4>
      <ul className="draft-list plain">
        {scenario.micro_goals.map((goal) => (
          <li key={goal.id}>
            <b>{goal.id}</b> {goal.description}
            <div className="muted">인정 근거 — {goal.required_evidence.join(" / ")}</div>
          </li>
        ))}
      </ul>

      <h4>첫 대사</h4>
      <p className="draft-line">마스코트: {scenario.mascot_intro}</p>
      <p className="draft-line">
        {scenario.character_name}: {scenario.character_opening_line}
      </p>
    </div>
  );
}

function StageGantt({
  timings,
  totalMs,
  models,
}: {
  timings: StageTiming[];
  totalMs: number;
  models: ModelInfo;
}) {
  const span = Math.max(totalMs, 1);
  const sorted = [...timings].sort((a, b) => a.start_offset_ms - b.start_offset_ms);
  return (
    <div className="gantt">
      {sorted.map((timing, index) => (
        <div className="gantt-row" key={`${timing.stage}-${index}`}>
          <span className="gantt-label">
            {stageLabel(timing.stage, models)}
            {timing.attempt_no ? ` ${timing.attempt_no}차` : ""}
          </span>
          <span className="gantt-track">
            <span
              className={`gantt-bar${timing.ok ? "" : " failed"}`}
              style={{
                marginLeft: `${(timing.start_offset_ms / span) * 100}%`,
                width: `${Math.max((timing.duration_ms / span) * 100, 1.2)}%`,
              }}
            />
          </span>
          <span className="gantt-ms">{timing.duration_ms}ms</span>
        </div>
      ))}
      <p className="hint">
        가로 위치가 턴 시작 기준 시각이다 — 겹쳐 있는 막대가 실제로 병렬로 돈 구간이다.
      </p>
    </div>
  );
}

function CandidateList({ candidates }: { candidates: CandidateRecord[] }) {
  if (candidates.length === 0) return <p className="muted">시도 없음</p>;
  return (
    <ol className="candidates">
      {candidates.map((candidate) => (
        <li key={candidate.attempt_no} className={candidate.delivered ? "delivered" : "rejected"}>
          <div className="candidate-head">
            <span className={`tag outcome-${candidate.outcome}`}>
              {OUTCOME_LABEL[candidate.outcome] ?? candidate.outcome}
            </span>
            {candidate.delivered && <span className="tag ok">전달됨</span>}
            {candidate.referee_violations.map((violation) => (
              <span className="tag referee" key={violation}>
                심판: {REFEREE_VIOLATION_LABEL[violation] ?? violation}
              </span>
            ))}
            {candidate.moderation?.flagged && (
              <span
                className={`tag ${
                  candidate.moderation.severity === "HIGH" ||
                  candidate.moderation.severity === "CRITICAL"
                    ? "danger"
                    : "warn"
                }`}
              >
                안전 필터 걸림 · 심각도{" "}
                {SEVERITY_LABEL[candidate.moderation.severity] ?? candidate.moderation.severity}
              </span>
            )}
          </div>
          {candidate.mascot_text && (
            <p className="candidate-text">마스코트: {candidate.mascot_text}</p>
          )}
          <p className="candidate-text">캐릭터: {candidate.text || <em>(빈 응답)</em>}</p>
          {candidate.judge && (
            <p className="judge">
              판단: {candidate.judge.reason || "-"}
              {candidate.judge.failure_codes.length > 0 && (
                <> · 실패코드 {candidate.judge.failure_codes.join(", ")}</>
              )}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}

function TurnDetail({
  turn,
  goals,
  models,
}: {
  turn: TurnRecord;
  goals: MicroGoal[];
  models: ModelInfo;
}) {
  const goalTitle = (id: string) => goals.find((g) => g.id === id)?.description ?? id;
  const moderation = turn.moderation;

  return (
    <div className="turn-detail">
      <section>
        <h4>발화 판정</h4>
        <p>
          <span className="tag">
            {INTAKE_CATEGORY_LABEL[turn.intake.category] ?? turn.intake.category}
          </span>
          <span className="tag subtle">
            진행 {GOAL_PROGRESS_LABEL[turn.intake.goal_progress] ?? turn.intake.goal_progress}
          </span>
          {turn.intake.misunderstanding && <span className="tag warn">상황 오인</span>}
          {turn.intake.character_bind && <span className="tag warn">캐릭터 곤란</span>}
          {turn.intake.degraded && <span className="tag danger">판정 실패로 대체</span>}
        </p>
        <p className="reason">{turn.intake.reason}</p>
        {turn.intake.override_source && (
          <p className="override">오버라이드: {turn.intake.override_source}</p>
        )}
        {turn.child_input_stored !== turn.child_input && (
          <p className="masked">
            PII 마스킹: <s>{turn.child_input}</s> → {turn.child_input_stored}
          </p>
        )}
      </section>

      <section>
        <h4>안전 필터</h4>
        {!moderation.available ? (
          <p className="muted">확인하지 못함 (키 미설정 또는 호출 실패)</p>
        ) : (
          <p>
            <span className={`tag ${moderation.flagged ? "danger" : "ok"}`}>
              {moderation.flagged ? "위반 감지" : "위반 없음"}
            </span>
            <span className="tag subtle">
              심각도 {SEVERITY_LABEL[moderation.severity] ?? moderation.severity}
            </span>
            {moderation.categories.map((category) => (
              <span className="tag subtle" key={category}>
                {MODERATION_CATEGORY_LABEL[category] ?? category}{" "}
                {moderation.category_scores[category]?.toFixed(2) ?? ""}
              </span>
            ))}
          </p>
        )}
      </section>

      <section>
        <h4>마스코트</h4>
        <p>
          <span className="tag">{MASCOT_MODE_LABEL[turn.mascot_mode] ?? turn.mascot_mode}</span>
          {turn.focus_goal_id && (
            <span className="tag subtle">
              초점 {turn.focus_goal_id} ·{" "}
              {SUPPORT_LEVEL_LABEL[turn.support_level] ?? turn.support_level}
            </span>
          )}
          <span className="tag subtle">
            힌트{" "}
            {turn.hint_source === "MASCOT"
              ? "마스코트"
              : turn.hint_source === "CHARACTER"
                ? "캐릭터"
                : "없음"}
          </span>
        </p>
        {turn.mascot_line ? <p className="mascot-line">{turn.mascot_line}</p> : <p className="muted">개입 없음</p>}
      </section>

      <section>
        <h4>대사 시도 {turn.candidates.length}회</h4>
        <CandidateList candidates={turn.candidates} />
        {turn.fallback_used && <p className="danger-text">사전 승인된 기본 응답으로 대체됨</p>}
      </section>

      <section>
        <h4>목표 스캔</h4>
        <p className="muted">대상: {turn.goal_scan.scanned_goal_ids.join(", ") || "없음"}</p>
        {turn.goal_scan.accepted.length > 0 && (
          <ul className="accepted">
            {turn.goal_scan.accepted.map((id) => (
              <li key={id}>
                <span className="tag ok">인정</span> {id} — {goalTitle(id)}
              </li>
            ))}
          </ul>
        )}
        {turn.goal_scan.rejected.length > 0 && (
          <ul className="rejected-list">
            {turn.goal_scan.rejected.map((rejected, index) => (
              <li key={`${rejected.goal_id}-${index}`}>
                <span className="tag warn">기각</span> {rejected.goal_id}
                <div className="reason">“{rejected.evidence}” — {rejected.reason}</div>
              </li>
            ))}
          </ul>
        )}
        {turn.goal_scan.accepted.length === 0 && turn.goal_scan.rejected.length === 0 && (
          <p className="muted">이번 턴에 새로 인정된 근거 없음</p>
        )}
      </section>

      <section>
        <h4>단계별 소요 — 전체 {turn.total_ms}ms</h4>
        <StageGantt timings={turn.timings} totalMs={turn.total_ms} models={models} />
        {turn.degraded_stages.length > 0 && (
          <p className="danger-text">
            실패해서 대체한 단계: {turn.degraded_stages.map((s) => stageLabel(s)).join(", ")}
          </p>
        )}
      </section>
    </div>
  );
}

const SUPPORT_REASON_LABEL: Record<string, string> = {
  REPEATED_PARTIAL: "같은 자리에서 맴돎",
  HELP_REQUESTED: "아이가 도움을 요청",
  CANNOT_FORM_ANSWER: "답을 못 만듦",
  MISSED_CUE_REPEATEDLY: "단서를 계속 놓침",
  CONSECUTIVE_ACHIEVEMENT: "연속 달성",
  SELF_EXPLAINED: "스스로 설명함",
  APPLIED_WITHOUT_CHOICES: "선택지 없이 해냄",
};

function HintPanel({ hints }: { hints: HintSummary }) {
  return (
    <div className="hints">
      <p className="muted">
        도움받은 턴 <b>{hints.hinted_turns}</b>/{hints.turns} · 마스코트{" "}
        <b>{hints.from_mascot}</b> · 캐릭터 <b>{hints.from_character}</b> · 도움 없이{" "}
        <b>{hints.unhinted_turns}</b>
      </p>
      {hints.answer_revealed > 0 && (
        <p className="danger-text">정답까지 알려준 목표 {hints.answer_revealed}개 (S3 도달)</p>
      )}
      {hints.escalations.length === 0 ? (
        <p className="hint">도움 세기를 올린 적이 없습니다.</p>
      ) : (
        <ul className="draft-list plain">
          {hints.escalations.map((step, index) => (
            <li key={`${step.subgoal_id}-${index}`}>
              턴 {step.turn_number} · <b>{step.subgoal_id}</b> {step.from_level} →{" "}
              {step.to_level}
              <div className="muted">
                {SUPPORT_REASON_LABEL[step.reason_code] ?? step.reason_code}
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="hint">
        캐릭터 힌트는 S0 에서 캐릭터 대사에 목표 유도가 섞인 턴이다 — 마스코트만 세면 절반을
        놓친다.
      </p>
    </div>
  );
}

function GoalPanel({ goals, definitions }: { goals: GoalState[]; definitions: MicroGoal[] }) {
  return (
    <ul className="goal-panel">
      {goals.map((goal) => {
        const definition = definitions.find((d) => d.id === goal.goal_id);
        return (
          <li key={goal.goal_id} className={`goal ${goal.status}`}>
            <div className="goal-head">
              <strong>{goal.goal_id}</strong>
              <span className={`tag status-${goal.status}`}>
                {GOAL_STATUS_LABEL[goal.status] ?? goal.status}
              </span>
              <span className="tag subtle">
                {SUPPORT_LEVEL_LABEL[goal.support_level] ?? goal.support_level}
              </span>
              {goal.achieved_with_support && <span className="tag warn">정답 제공 후</span>}
            </div>
            <p className="goal-desc">{definition?.description}</p>
            {goal.evidence_text && (
              <div className="evidence">
                <div>근거: “{goal.evidence_text}” (턴 {goal.achieved_at_turn})</div>
                <div className="reason">{goal.why_this_satisfies}</div>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export default function App() {
  const [scenarios, setScenarios] = useState<ScenarioOption[]>([]);
  const [scenarioKey, setScenarioKey] = useState("fall");
  const [session, setSession] = useState<SessionView | null>(null);
  const [turns, setTurns] = useState<TurnRecord[]>([]);
  const [openTurn, setOpenTurn] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"console" | "pipeline" | "archive">("console");
  const [models, setModels] = useState<ModelInfo | null>(null);
  const [theme, setTheme] = useState("");
  const [level, setLevel] = useState("L2");
  const [drafting, setDrafting] = useState(false);
  const [justCreatedKey, setJustCreatedKey] = useState<string | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // 목록은 서버가 원본이다. 다른 창에서 만들었거나 서버가 재시작됐으면 화면이 뒤처지므로
  // 첫 로드뿐 아니라 목록을 열 때마다 다시 읽는다.
  const refreshScenarios = useCallback(async () => {
    try {
      const data = await listScenarios();
      const list = data.scenarios;
      setScenarios(list);
      setModels(data.models);
      // 고른 시나리오가 서버에서 사라졌으면(재시작 등) 첫 항목으로 되돌린다.
      setScenarioKey((current) =>
        list.some((s) => s.key === current) ? current : (list[0]?.key ?? current),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refreshScenarios();
  }, [refreshScenarios]);

  useEffect(() => {
    // block:"nearest" 가 없으면 대화창이 아니라 페이지 전체가 스크롤돼서
    // 헤더와 목표 패널이 화면 밖으로 밀려난다.
    chatEndRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [session?.transcript.length, busy]);

  async function runDraft() {
    const input = theme.trim();
    if (!input || drafting) return;
    setError(null);
    setJustCreatedKey(null);
    setDrafting(true);
    try {
      const created = await generateScenario(input, level);
      await refreshScenarios();
      setScenarioKey(created.key);
      setTheme("");
      setJustCreatedKey(created.key);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDrafting(false);
    }
  }

  async function removeScenario() {
    if (!selectedScenario?.generated || busy) return;
    const used = selectedScenario.key === session?.scenario.scenario_id;
    const warning = used ? "\n\n지금 열려 있는 세션도 함께 닫힙니다." : "";
    if (!window.confirm(`“${selectedScenario.title}” 을 지울까요?${warning}`)) return;

    setError(null);
    setBusy(true);
    try {
      const result = await deleteScenario(selectedScenario.key);
      if (used) setSession(null);
      if (justCreatedKey === result.deleted) setJustCreatedKey(null);
      await refreshScenarios();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    setError(null);
    setBusy(true);
    try {
      const created = await createSession(scenarioKey);
      setSession(created);
      setTurns([]);
      setOpenTurn(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    const utterance = input.trim();
    if (!session || !utterance || busy) return;
    setError(null);
    setBusy(true);
    setInput("");
    try {
      const result = await postTurn(session.session_id, utterance);
      setSession(result.session);
      setTurns((previous) => [...previous, result.turn]);
      setOpenTurn(result.turn.turn_index);
    } catch (e) {
      setError((e as Error).message);
      setInput(utterance);
    } finally {
      setBusy(false);
    }
  }

  const ended = session?.phase === "ENDED";
  const selectedScenario = scenarios.find((s) => s.key === scenarioKey) ?? null;

  return (
    <div className="app">
      <header>
        <h1>느링고 역할극 PoC 콘솔</h1>
        <nav className="tabs">
          <button
            className={view === "console" ? "tab on" : "tab"}
            onClick={() => setView("console")}
          >
            역할극 콘솔
          </button>
          <button
            className={view === "pipeline" ? "tab on" : "tab"}
            onClick={() => setView("pipeline")}
          >
            파이프라인 구조
          </button>
          <button
            className={view === "archive" ? "tab on" : "tab"}
            onClick={() => setView("archive")}
          >
            지난 역할극
          </button>
        </nav>
        {view === "console" && (
          <div className="controls">
            <select
              value={scenarioKey}
              onChange={(e) => setScenarioKey(e.target.value)}
              onFocus={() => void refreshScenarios()}
              disabled={busy}
            >
              {scenarios.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.title}
                </option>
              ))}
            </select>
            <button onClick={start} disabled={busy || scenarios.length === 0}>
              {session ? "새 세션" : "세션 시작"}
            </button>
            {selectedScenario?.generated && (
              <button className="ghost danger" onClick={removeScenario} disabled={busy}>
                삭제
              </button>
            )}
          </div>
        )}
      </header>

      {view === "console" && (
        <div className="draft-bar">
          <input
            value={theme}
            placeholder="새 시나리오 주제 — 예: 친구가 실수로 그림을 찢었다"
            onChange={(e) => setTheme(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) runDraft();
            }}
            disabled={drafting}
          />
          <select value={level} onChange={(e) => setLevel(e.target.value)} disabled={drafting}>
            <option value="L1">L1 단순</option>
            <option value="L2">L2 보통</option>
            <option value="L3">L3 복합 감정</option>
          </select>
          <button onClick={runDraft} disabled={drafting || !theme.trim()}>
            {drafting ? "만드는 중…" : "AI로 시나리오 만들기"}
          </button>
        </div>
      )}

      {view === "console" && selectedScenario && (
        <ScenarioDetail
          scenario={selectedScenario}
          models={models}
          justCreated={selectedScenario.key === justCreatedKey}
        />
      )}

      {error && <div className="error">{error}</div>}

      {view === "archive" && <ArchiveView />}

      {view === "pipeline" ? (
        <section className="pipeline-page">
          <h2>한 턴에 어떤 모델이 몇 번 불리나</h2>
          <p className="muted">
            아래 모델명은 서버가 실제로 쓰는 설정값이다. 세로선 하나가 참여자 하나이고, 가로
            화살표 하나가 실제 호출 한 번이다.
          </p>
          {models ? (
            <SequenceDiagram models={models} />
          ) : (
            <p className="muted">모델 정보를 불러오는 중…</p>
          )}
          <CodeSnippets />
        </section>
      ) : view === "archive" ? null : !session ? (
        <p className="empty">
          {scenarios.length === 0
            ? "저장된 시나리오가 없습니다. 위에 주제를 적고 “AI로 시나리오 만들기”를 누르세요."
            : "시나리오를 고르고 세션을 시작하세요."}
        </p>
      ) : (
        <main>
          <div className="chat-pane">
            <div className="scenario-head">
              <strong>{session.scenario.title}</strong>
              <span className="tag subtle">난이도 {session.scenario.scenario_level}</span>
              <span className="tag subtle">
                턴 {session.turn_index}/{session.scenario.maximum_turn_count}
              </span>
              {ended && (
                <span className="tag ok">
                  종료 · {END_REASON_LABEL[session.end_reason ?? ""] ?? session.end_reason}
                </span>
              )}
            </div>

            <div className="transcript">
              {session.transcript.map((utterance, index) => (
                <div key={index} className={`bubble ${utterance.speaker}`}>
                  <span className="who">
                    {utterance.speaker === "CHILD"
                      ? "아이"
                      : utterance.speaker === "MASCOT"
                        ? "마스코트"
                        : session.scenario.character_name}
                  </span>
                  <p>{utterance.text}</p>
                </div>
              ))}
              {busy && <div className="bubble pending">생각하는 중…</div>}
              <div ref={chatEndRef} />
            </div>

            <div className="composer">
              {/* Enter 처리: 한글 IME 조합 중의 Enter 는 글자를 확정하는 키라
                  isComposing 을 거르지 않으면 조합이 덜 끝난 채 전송되거나 전송이 씹힌다. */}
              <input
                value={input}
                placeholder={ended ? "세션이 끝났습니다" : "아이로서 말해 보세요"}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing) send();
                }}
                disabled={busy || ended}
              />
              <button onClick={send} disabled={busy || ended || !input.trim()}>
                보내기
              </button>
            </div>
          </div>

          <aside className="observe-pane">
            <section className="summary">
              <h3>마이크로 목표</h3>
              <p className="muted">
                달성 {session.summary.achieved}/{session.summary.total} · 스스로 도달{" "}
                {session.summary.achieved_without_answer_reveal} · 유도 소진{" "}
                {session.summary.abandoned}
              </p>
              <GoalPanel goals={session.goals} definitions={session.scenario.micro_goals} />
            </section>

            <section>
              <h3>도움 집계</h3>
              <HintPanel hints={session.hints} />
            </section>

            <section>
              <h3>턴 기록</h3>
              {turns.length === 0 && <p className="muted">아직 턴이 없습니다.</p>}
              {turns
                .slice()
                .reverse()
                .map((turn) => (
                  <div className="turn-card" key={turn.turn_index}>
                    <button
                      className="turn-toggle"
                      onClick={() =>
                        setOpenTurn(openTurn === turn.turn_index ? null : turn.turn_index)
                      }
                    >
                      <span>턴 {turn.turn_index}</span>
                      <span className="tag subtle">{turn.total_ms}ms</span>
                      <span className="tag subtle">
                        {INTAKE_CATEGORY_LABEL[turn.intake.category] ?? turn.intake.category}
                      </span>
                      {turn.mascot_mode !== "NONE" && (
                        <span className="tag warn">
                          {MASCOT_MODE_LABEL[turn.mascot_mode] ?? turn.mascot_mode}
                        </span>
                      )}
                      {turn.goal_scan.accepted.length > 0 && (
                        <span className="tag ok">+{turn.goal_scan.accepted.length}</span>
                      )}
                      {turn.candidates.length > 1 && (
                        <span className="tag">시도 {turn.candidates.length}회</span>
                      )}
                    </button>
                    {openTurn === turn.turn_index && (
                      <TurnDetail
                        turn={turn}
                        goals={session.scenario.micro_goals}
                        models={session.models}
                      />
                    )}
                  </div>
                ))}
            </section>
          </aside>
        </main>
      )}
    </div>
  );
}
