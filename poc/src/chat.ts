import readline from "node:readline";
import { stdin, stdout } from "node:process";
import dotenv from "dotenv";
import { scenario, type MicroGoal, type Scenario } from "./fixtures/scenario.js";
import { scenarioJealousy } from "./fixtures/scenarioJealousy.js";
import { friendPersona, awardWinnerPersona, type Persona } from "./fixtures/persona.js";
import { buildRegistry, availableProviders } from "./providers/registry.js";
import { moderationSeverity } from "./providers/moderation.js";
import type { ModerationClient, ModerationResult, Provider } from "./providers/types.js";
import { safeCall, tryParseJson } from "./util.js";

dotenv.config();

// 대화형 수동 테스트용 스크립트. 실제 느링고 흐름(생성 → 판단 에이전트 검증 → PASS만 전달,
// 아니면 재생성)을 재현한다. 자동 채점 없이 직접 쳐보고 캐릭터 톤/판단 게이트 동작을 눈으로 확인하는 용도.
// 07-persona.ts의 가정(persona.ts)을 그대로 이어서 쓴다 — 정식 스펙 아님.

const SCENARIOS: Record<string, { scenario: Scenario; persona: Persona }> = {
  fall: { scenario, persona: friendPersona },
  jealousy: { scenario: scenarioJealousy, persona: awardWinnerPersona },
};

// EVAL-09,10 재현: 후보 기준 최대 3개까지 검증 후 안전한 기본 응답(SAFE_FALLBACK)으로 전환.
const MAX_CANDIDATES = 3;

// SAFE-05: 안전 대응 문구는 사전에 검토된 고정 문구를 사용할 수 있어야 한다.
const SAFE_FALLBACK_LINE = "(친구가 잠시 멈칫한다) 음... 나 잠깐 다른 얘기 하고 싶어. 다시 물어봐 줄래?";

// 마스코트(도우미 강아지)의 개입 문구 — 화자를 캐릭터와 분리해서 짧게 제지·환기만 하고,
// 캐릭터의 반응은 그대로 이어서 생성한다(마스코트가 캐릭터를 대신하지 않음).
const MASCOT_PROFANITY_LINE = "(마스코트가 옆에서 살짝 끼어든다) 얘들아, 그런 말은 마음을 아프게 할 수 있어~";
const MASCOT_OFF_TOPIC_LINE = "(마스코트가 톡톡 건드린다) 잠깐! 지금은 다른 이야기 중이었지? 다시 돌아가 볼까?";
// HIGH 등급(Moderation이 구체적 위협·그래픽 폭력 등으로 잡은 경우) — PROFANITY보다 더
// 분명하게 개입한다. RISK(역할극 중단)까지는 아니고, 역할극은 이어가되 확실히 짚고 넘어간다.
const MASCOT_HIGH_CONCERN_LINE = "(마스코트가 앞으로 나선다) 잠깐, 그런 표현은 안 돼. 서로 다치게 하는 말은 하지 말자, 알겠지?";

function buildGenerationSystemPrompt(scenario: Scenario, persona: Persona): string {
  return `너는 역할극 캐릭터 "${persona.name}"이다.
성격: ${persona.personality}
말투: ${persona.speechStyle}
규칙: ${persona.firstPersonRule}
시나리오 사실: ${scenario.scenarioFacts.join(" ")}
금지: ${scenario.prohibitedInferences.join(" ")}
캐릭터로서 한두 문장만 짧게 반응하라. 설명하지 말고 대사만 출력하라.`;
}

// 12.3 판단 기준(안전성/개인정보, 시나리오 사실, 캐릭터 유지, 대화 적절성) 재현.
function buildJudgeSystemPrompt(scenario: Scenario, persona: Persona): string {
  return `너는 느링고의 응답 판단 에이전트다. 아래 후보 대사가 아동에게 그대로 전달되어도 되는지 판정하라.
시나리오 사실: ${scenario.scenarioFacts.join(" ")}
금지 추론: ${scenario.prohibitedInferences.join(" ")}
캐릭터 성격/말투: ${persona.personality} / ${persona.speechStyle}
판정 기준:
1. 안전성: 욕설, 조롱, 위협, 차별, 연령 부적합 내용, 부적절한 개인정보 요구가 없어야 한다.
2. 시나리오 일치: 시나리오 사실과 모순되거나 금지 추론을 사실처럼 단정하지 않아야 한다.
3. 캐릭터 유지: 1인칭 캐릭터 톤과 말투를 유지해야 한다 (상황을 설명하는 선생님처럼 되면 안 된다).
4. 대화 적절성: 한 번에 하나의 생각만 담고, 지나치게 길지 않아야 한다.
반드시 아래 JSON 형식으로만 답하라. 다른 텍스트를 덧붙이지 마라.
{
  "safe_to_send": boolean,
  "decision": "PASS" | "REGENERATE" | "SAFETY_REGENERATE",
  "failure_codes": string[],
  "reason": "한 문장",
  "revision_instruction": {"change": string[], "avoid": string[]}
}`;
}

interface JudgeOutput {
  safe_to_send?: boolean;
  decision?: string;
  failure_codes?: string[];
  reason?: string;
  revision_instruction?: { change?: string[]; avoid?: string[] };
}

interface DeliveredReply {
  text: string;
  /** true면 자동 검증을 통과하지 못해 사전 승인된 기본 응답으로 대체된 경우 */
  fallbackUsed: boolean;
  candidateAttempts: number;
}

async function generateApprovedReply(
  provider: Provider,
  moderation: ModerationClient,
  scenario: Scenario,
  persona: Persona,
  userInput: string,
): Promise<DeliveredReply> {
  const generationSystemPrompt = buildGenerationSystemPrompt(scenario, persona);
  const judgeSystemPrompt = buildJudgeSystemPrompt(scenario, persona);

  let revisionNote = "";

  for (let attempt = 1; attempt <= MAX_CANDIDATES; attempt++) {
    const candidateResult = await safeCall(() =>
      provider.complete({
        system: generationSystemPrompt,
        user: revisionNote ? `${userInput}\n\n[이전 후보 수정 지시]\n${revisionNote}` : userInput,
      }),
    );

    if (!candidateResult.ok) {
      console.log(`  [생성 오류 attempt ${attempt}] ${candidateResult.error}`);
      continue;
    }

    const candidateText = candidateResult.value.text.trim();
    if (!candidateText) {
      console.log(`  [빈 응답 attempt ${attempt}] 재생성`);
      continue;
    }
    // 반려되더라도 이 초안 자체는 여기 한 번만 찍힌다 — PASS면 밑에서 "친구>"로 다시 보이고,
    // 반려면 이 줄이 "그때 원래 뭐라고 하려던 후보였는지"를 볼 수 있는 유일한 자리다.
    console.log(`  [초안 attempt ${attempt}] ${candidateText}`);

    // SAFE-01: AI 출력도 아동 입력과 동일하게 안전 검사 대상 — LLM 판단만 믿지 않고
    // OpenAI Moderation으로 이중 확인한다 (한쪽이 놓쳐도 다른 쪽이 잡도록 하는 defense-in-depth).
    // 이 둘도 서로 독립적이라 동시에 호출한다.
    const [moderationResult, judgeResult] = await Promise.all([
      moderation.available ? safeCall(() => moderation.moderate(candidateText)) : Promise.resolve(null),
      safeCall(() => provider.complete({ system: judgeSystemPrompt, user: candidateText })),
    ]);
    const moderationFlagged = moderationResult?.ok === true && moderationResult.value.flagged;

    if (!judgeResult.ok) {
      // 판단 자체가 기술적으로 실패한 경우 (EVAL-06): 후보를 폐기하고 다시 생성.
      console.log(`  [판단 호출 오류 attempt ${attempt}] ${judgeResult.error}`);
      continue;
    }

    const judge = tryParseJson<JudgeOutput>(judgeResult.value.text);
    const safeToSend = judge?.safe_to_send === true && !moderationFlagged;
    const decision = moderationFlagged ? "SAFETY_REGENERATE" : (judge?.decision ?? "PARSE_ERROR");

    const moderationNote = moderation.available
      ? formatModerationDetail(moderationResult?.ok === true ? moderationResult.value : null)
      : "";

    if (safeToSend && decision === "PASS") {
      console.log(`  [판단: PASS, attempt ${attempt}] ${judge?.reason ?? ""}${moderationNote}`);
      return { text: candidateText, fallbackUsed: false, candidateAttempts: attempt };
    }

    if (moderationFlagged) {
      console.log(`  [판단: SAFETY_REGENERATE, attempt ${attempt}] Moderation이 AI 후보를 유해로 판정${moderationNote}`);
    } else {
      console.log(
        `  [판단: ${decision}, attempt ${attempt}] ${judge?.reason ?? "판정 파싱 실패"} — failure_codes: ${(judge?.failure_codes ?? []).join(", ") || "-"}`,
      );
    }

    const change = judge?.revision_instruction?.change ?? [];
    const avoid = judge?.revision_instruction?.avoid ?? [];
    revisionNote = moderationFlagged
      ? "변경할 것: 안전한 표현으로 다시 작성 / 피할 것: 폭력적·위협적·조롱하는 표현"
      : `변경할 것: ${change.join(", ") || "-"} / 피할 것: ${avoid.join(", ") || "-"}`;
  }

  console.log(`  [SAFE_FALLBACK] 재시도 한도(${MAX_CANDIDATES}) 초과 — 사전 승인된 기본 응답으로 전환`);
  return { text: SAFE_FALLBACK_LINE, fallbackUsed: true, candidateAttempts: MAX_CANDIDATES };
}

// SAFE-01,02,03,04 / INP-10 재현: AI 출력뿐 아니라 아동 입력도 안전 검사 대상이다.
// SAFE-02는 자해·타해를 같은 위험 신호로 묶어서 다룬다 — 자신을 향한 것이든("죽고 싶어")
// 남을 향한 것이든("죽어버려") 죽음·신체적 해악을 언급하면 동일하게 RISK로 escalate한다.
// 대상이 자신인지 남인지에 따라 심각도를 다르게 보지 않는다(둘 다 즉시 보호 절차 우선).
function buildInputSafetyJudgePrompt(scenario: Scenario): string {
  return `너는 느링고의 입력 안전 판단자다. 아동이 방금 입력한 문장 하나를 분류하라. 아래 카테고리 중 하나만 골라야 한다.
이 역할극의 시나리오 사실: ${scenario.scenarioFacts.join(" ")}
- NORMAL: 안전한 일반 발화. 위 시나리오 상황에 자연스럽게 나올 수 있는 걱정·관찰·질문
  (예: 상대의 상태를 묻거나 걱정하는 말)은 항상 NORMAL이다 — 죽음이나 폭력을 바라는 의도가 없는 한 그렇다.
- PROFANITY: 단순 욕설·비하 표현(예: "바보", "멍청이")이지만 죽음이나 신체적 해악을 바라지 않음
- PII: 이름, 전화번호, 주소 등 개인정보가 포함됨
- RISK: 자신이든 남이든 죽음·자해·신체적 해악을 스스로 "바라거나 가하겠다"는 의도가 담긴 표현만 해당
  (예: "죽고 싶어", "죽어버려", "때릴 거야"). 대상이 나 자신인지 상대방인지는 분류에 영향을 주지 않는다.
  단순히 상대가 이미 다쳤는지 묻거나 걱정하는 말은 RISK가 아니다 — 반드시 의도·바람이 담겨야 RISK다.
- OFF_TOPIC: 위 시나리오 사실과 전혀 관련 없는 화제(예: 게임, 저녁 메뉴, 다른 친구 이야기 등
  이 상황과 무관한 이야기). 시나리오 상황에 대한 질문·반응이면 OFF_TOPIC이 아니다.
반드시 아래 JSON 형식으로만 답하라. 다른 텍스트를 덧붙이지 마라.
{"category": "NORMAL" | "PROFANITY" | "PII" | "RISK" | "OFF_TOPIC", "reason": "한 문장"}`;
}

function buildPiiMaskPrompt(): string {
  return `너는 느링고의 입력 정제기다. 아동 발화에서 이름, 전화번호, 주소 등
학습 판단에 필요 없는 개인정보를 제거하거나 마스킹하고, 의미는 보존한 문장 한 줄만 출력하라.
다른 설명 없이 정제된 문장만 출력하라.`;
}

interface InputSafetyOutput {
  category?: "NORMAL" | "PROFANITY" | "PII" | "RISK" | "OFF_TOPIC";
  reason?: string;
}

interface InputSafetyResult {
  category: string;
  reason: string;
  moderationFlagged: boolean | null;
  moderationDetail: string;
}

// 로그에 카테고리별 확률 점수까지 보이게 — 그냥 "flagged=true"만으로는 뭐가 얼마나
// 위험하다고 봤는지 알 수 없다.
function formatModerationDetail(result: ModerationResult | null): string {
  if (!result) return "";
  if (!result.flagged) return " (Moderation flagged=false)";
  const scores = result.categories.map((c) => `${c}=${result.categoryScores[c]?.toFixed(2)}`).join(" ");
  return ` (Moderation flagged=true, severity=${moderationSeverity(result.categories)}, ${scores})`;
}

async function checkChildInputSafety(
  provider: Provider,
  moderation: ModerationClient,
  scenario: Scenario,
  text: string,
): Promise<InputSafetyResult> {
  // Moderation과 LLM 판단은 서로 결과를 필요로 하지 않으니 순차 대기하지 않고 동시에 호출한다.
  const [moderationResult, judgeResult] = await Promise.all([
    moderation.available ? safeCall(() => moderation.moderate(text)) : Promise.resolve(null),
    safeCall(() => provider.complete({ system: buildInputSafetyJudgePrompt(scenario), user: text })),
  ]);

  let category = "PARSE_ERROR";
  let reason = "판정 파싱 실패";
  if (judgeResult.ok) {
    const parsed = tryParseJson<InputSafetyOutput>(judgeResult.value.text);
    category = parsed?.category ?? "PARSE_ERROR";
    reason = parsed?.reason ?? reason;
  } else {
    reason = `판단 호출 오류: ${judgeResult.error}`;
  }

  const moderatedOk = moderationResult?.ok === true ? moderationResult.value : null;
  const severity = moderatedOk ? moderationSeverity(moderatedOk.categories) : "NONE";
  // CRITICAL(자해/아동성적 등)은 우리 자체 분류기가 뭐라 판정했든 무조건 RISK로 강제한다 —
  // defense-in-depth: 한쪽이 놓쳐도 다른 쪽이 잡도록 하는 이중 안전망.
  if (severity === "CRITICAL" && category !== "RISK") {
    reason = `Moderation이 CRITICAL(${moderatedOk!.categories.join(", ")})로 판정 — 자체 분류(${category})보다 우선하여 RISK로 강제`;
    category = "RISK";
  } else if (severity === "HIGH" && category !== "RISK" && category !== "HIGH_CONCERN") {
    // HIGH(구체적 위협·그래픽 폭력 등)는 역할극을 완전히 멈추진 않되(RISK만큼은 아님),
    // 마스코트가 개입해야 하는 수준으로 격상한다 — 자체 분류기가 PROFANITY/NORMAL로 봤어도 덮어씀.
    reason = `Moderation이 HIGH(${moderatedOk!.categories.join(", ")})로 판정 — 자체 분류(${category})보다 우선하여 HIGH_CONCERN으로 격상`;
    category = "HIGH_CONCERN";
  }

  const moderationFlagged = !moderationResult ? null : moderationResult.ok ? moderationResult.value.flagged : null;
  return { category, reason, moderationFlagged, moderationDetail: formatModerationDetail(moderatedOk) };
}

async function maskPii(provider: Provider, text: string): Promise<string> {
  const result = await safeCall(() => provider.complete({ system: buildPiiMaskPrompt(), user: text }));
  return result.ok ? result.value.text.trim() : text;
}

// "역할극에 대해" 문서 §5 재현: "이번 턴은 이 목표만" 식으로 좁혀 보지 않고, 매 턴 지금까지의
// 대화 전체를 놓고 아직 안 끝난 마이크로 목표를 전부 다시 스캔한다(누적 판단). ANA-01~09와
// 달리 순서 상관없이 아이가 먼저 말해버린 것도 인정하기 위함 — 정식 스펙 아니라 PoC 가정.
function buildMicroGoalScanPrompt(pendingGoals: MicroGoal[]): string {
  const goalList = pendingGoals
    .map((g) => `- ${g.id}: ${g.description} (증거: ${g.requiredEvidence.join(", ")})`)
    .join("\n");
  return `너는 느링고의 원인 판단 에이전트다. 지금까지의 전체 대화를 보고, 아래 아직 달성되지 않은
마이크로 목표 중 "아이" 발화에서 실제로 증거가 나타난 것이 있는지 전부 다시 스캔하라.
목표 하나에만 집중하지 말고 목록 전체를 매번 확인하라 — 아이가 순서와 상관없이 먼저 말했어도 인정한다.
반드시 "아이:"로 표시된 발화에서 나온 증거만 인정하라. "친구:"로 표시된 캐릭터 자신의 대사는
아무리 정답에 가까운 내용을 말해도 증거로 인정하지 않는다 — 이건 아이가 스스로 이해했는지
판단하는 것이지, 캐릭터가 힌트를 줬는지 판단하는 게 아니다.
${goalList}
반드시 아래 JSON 형식으로만 답하라. 다른 텍스트를 덧붙이지 마라.
{"achieved": [{"micro_goal_id": string, "evidence": string}]} (evidence에는 근거가 된 아이 발화 원문을 그대로 넣어라)
증거가 아직 없으면 achieved를 빈 배열로 둬라.`;
}

interface MicroGoalScanOutput {
  achieved?: { micro_goal_id?: string; evidence?: string }[];
}

async function scanMicroGoals(
  provider: Provider,
  pendingGoals: MicroGoal[],
  fullHistoryText: string,
): Promise<{ achievedIds: string[]; evidenceById: Map<string, string>; latencyMs: number }> {
  const start = Date.now();
  if (pendingGoals.length === 0) {
    return { achievedIds: [], evidenceById: new Map(), latencyMs: 0 };
  }
  const result = await safeCall(() =>
    provider.complete({ system: buildMicroGoalScanPrompt(pendingGoals), user: fullHistoryText }),
  );
  const latencyMs = Date.now() - start;
  if (!result.ok) {
    return { achievedIds: [], evidenceById: new Map(), latencyMs };
  }
  const parsed = tryParseJson<MicroGoalScanOutput>(result.value.text);
  const evidenceById = new Map<string, string>();
  const achievedIds: string[] = [];
  for (const item of parsed?.achieved ?? []) {
    if (item.micro_goal_id && pendingGoals.some((g) => g.id === item.micro_goal_id)) {
      achievedIds.push(item.micro_goal_id);
      evidenceById.set(item.micro_goal_id, item.evidence ?? "");
    }
  }
  return { achievedIds, evidenceById, latencyMs };
}

// node:readline/promises의 question()은 호출된 그 순간에만 'line' 리스너를 붙인다 —
// AI 응답을 기다리는 동안(수 초) 사용자가 입력을 미리 쳐 두면, 그 사이 도착한 줄은
// 리스너 없이 그냥 emit되어 유실된다. 큐로 직접 받아서 이 유실을 막는다.
function createLineReader(rl: readline.Interface) {
  const queue: string[] = [];
  const waiters: ((line: string | null) => void)[] = [];
  let closed = false;

  rl.on("line", (line) => {
    const waiter = waiters.shift();
    if (waiter) {
      waiter(line);
    } else {
      queue.push(line);
    }
  });
  rl.on("close", () => {
    closed = true;
    let waiter: ((line: string | null) => void) | undefined;
    while ((waiter = waiters.shift())) waiter(null);
  });

  return function readLine(): Promise<string | null> {
    if (queue.length > 0) return Promise.resolve(queue.shift()!);
    if (closed) return Promise.resolve(null);
    return new Promise((resolve) => waiters.push(resolve));
  };
}

function pickProvider(providers: Provider[], requestedId: string | undefined): Provider | null {
  if (requestedId) {
    return providers.find((p) => p.id === requestedId) ?? null;
  }
  return providers[0] ?? null;
}

async function main() {
  const registry = buildRegistry();
  const available = availableProviders(registry);

  const arg = process.argv.find((a) => a.startsWith("--provider="));
  const requestedId = arg?.replace("--provider=", "");
  const scenarioArg = process.argv.find((a) => a.startsWith("--scenario="))?.replace("--scenario=", "");
  const scenarioKey = scenarioArg && SCENARIOS[scenarioArg] ? scenarioArg : "fall";
  if (scenarioArg && !SCENARIOS[scenarioArg]) {
    console.log(`scenario "${scenarioArg}"를 찾을 수 없음 — 사용 가능: ${Object.keys(SCENARIOS).join(", ")}. 기본값(fall) 사용.`);
  }
  const { scenario: activeScenario, persona: activePersona } = SCENARIOS[scenarioKey];

  if (available.length === 0) {
    console.log("사용 가능한 provider가 없음 — .env에 최소 하나의 API 키를 채워야 함.");
    return;
  }

  const provider = pickProvider(available, requestedId);
  if (!provider) {
    console.log(
      `provider "${requestedId}"를 찾을 수 없거나 키가 없음. 사용 가능: ${available.map((p) => p.id).join(", ")}`,
    );
    return;
  }

  console.log(`=== 느링고 역할극 수동 테스트 (${provider.label}, 시나리오: ${activeScenario.title} [${activeScenario.scenarioLevel}]) ===`);
  console.log(`캐릭터: ${activePersona.name} (${activePersona.personality})`);
  console.log(`시작 상황: ${activeScenario.scenarioFacts.join(" ")}`);
  console.log(`생성 → 판단 에이전트(PASS만 전달, 안전 문제는 재생성/기본응답) 흐름 재현.`);
  console.log(`"exit" 또는 "quit" 입력하면 종료.\n`);

  const transcript: { speaker: "아이" | "친구"; text: string }[] = [];
  const microGoalState = new Map(activeScenario.microGoals.map((mg) => [mg.id, false]));

  // 마스코트(도우미 강아지)와 캐릭터(친구)는 서로 다른 화자다 — 장면을 소개하는 3인칭 설명·질문은
  // 마스코트 몫이고, 캐릭터는 항상 1인칭으로만 말한다. 마스코트는 매 턴 나오는 게 아니라
  // 필요할 때만(세션 시작, 안전 개입 등) 나올 수도, 안 나올 수도 있다 — 세션 시작 시 한 번만 표시.
  console.log(`마스코트> ${activeScenario.mascotIntro}`);
  console.log(`친구> ${activeScenario.characterOpeningLine}`);
  transcript.push({ speaker: "친구", text: activeScenario.characterOpeningLine });

  const rl = readline.createInterface({ input: stdin, output: stdout });
  const readLine = createLineReader(rl);

  try {
    while (true) {
      stdout.write("아이> ");
      const line = await readLine();
      if (line === null) {
        // 입력 스트림이 끊긴 경우(예: 파이프 입력 종료) 조용히 종료
        break;
      }
      const trimmed = line.trim();
      if (trimmed === "exit" || trimmed === "quit" || trimmed === "종료") {
        break;
      }
      if (trimmed === "") continue;

      // 전체 턴 시간: 아동 입력 안전검사부터 최종 전달까지 실제 체감 왕복시간 전부 포함.
      const turnStart = Date.now();

      const inputSafety = await checkChildInputSafety(provider, registry.moderation, activeScenario, trimmed);
      console.log(`  [입력 안전: ${inputSafety.category}] ${inputSafety.reason}${inputSafety.moderationDetail}`);

      if (inputSafety.category === "RISK") {
        // SAFE-04 / SAFETY_ESCALATION 재현: 일반 역할극보다 보호 절차를 우선한다.
        // 이 발화는 역할극 transcript에 포함하지 않는다 (SAFE-06: 안전 사건은 일반 학습 기록과 분리).
        // 화자는 마스코트다 — 캐릭터(친구)가 아니라 도우미 강아지가 개입하는 상황.
        console.log(
          `마스코트> 잠깐, 지금 이야기는 선생님이나 어른한테 꼭 알려야 할 것 같아. 잠시 여기서 멈출게.`,
        );
        continue;
      }

      // 욕설·주제 이탈·HIGH 수준 우려는 역할극을 중단하지 않되(SAFE-03), 캐릭터가 아니라
      // 마스코트가 짧게 제지·환기만 하고 빠진다 — 캐릭터의 반응은 이어서 그대로 생성된다.
      if (inputSafety.category === "PROFANITY") {
        console.log(`마스코트> ${MASCOT_PROFANITY_LINE}`);
      } else if (inputSafety.category === "OFF_TOPIC") {
        console.log(`마스코트> ${MASCOT_OFF_TOPIC_LINE}`);
      } else if (inputSafety.category === "HIGH_CONCERN") {
        console.log(`마스코트> ${MASCOT_HIGH_CONCERN_LINE}`);
      }

      let contentForTranscript = trimmed;
      if (inputSafety.category === "PII") {
        // INP-10 / PRI-04 재현: 분석에 필요 없는 개인정보는 이후 단계로 넘기기 전에 마스킹한다.
        contentForTranscript = await maskPii(provider, trimmed);
        console.log(`  [PII 마스킹] "${trimmed}" → "${contentForTranscript}"`);
      }

      transcript.push({ speaker: "아이", text: contentForTranscript });

      const historyText = transcript.map((t) => `${t.speaker}: ${t.text}`).join("\n");
      // SAFE-03: 단순 욕설은 역할극을 중단하지 않되, 그 감정을 안전한 표현으로 전환하도록
      // 캐릭터가 "도와야" 한다 — 우연히 그렇게 반응하길 기대하지 않고 생성 단계에 명시적으로 지시한다.
      const profanityCoachingHint =
        inputSafety.category === "PROFANITY"
          ? `\n\n[안전 지침] 아이가 방금 욕설을 썼다. 그 말을 그대로 맞받아치거나 따라 하지 말고, 아이가 느끼는 답답함이나 화남을 캐릭터로서 안전한 말로 표현하도록 부드럽게 도와줘.`
          : "";
      // 주제 이탈도 역할극을 중단하지 않고(마스코트가 이미 환기했으니), 캐릭터는 발화를
      // 짧게 수용한 뒤 시나리오로 자연스럽게 복귀한다.
      const offTopicRedirectHint =
        inputSafety.category === "OFF_TOPIC"
          ? `\n\n[안전 지침] 아이가 지금 상황과 관계없는 이야기를 했다. 그 말을 짧게 받아준 뒤, 자연스럽게 지금 상황으로 다시 돌아오는 대사를 해라.`
          : "";
      // HIGH_CONCERN: 마스코트가 이미 확실히 짚었으니, 캐릭터는 그 표현을 절대 따라 하거나
      // 되풀이하지 않고 안전하게 진정하는 방향으로만 반응한다.
      const highConcernHint =
        inputSafety.category === "HIGH_CONCERN"
          ? `\n\n[안전 지침] 아이가 방금 심각한 표현을 썼다(마스코트가 이미 제지했다). 그 표현을 절대 반복하거나 언급하지 말고, 캐릭터가 진정하며 안전하게 반응하는 짧은 대사만 만들어라.`
          : "";

      // GEN-02 재현: 이번 턴 시작 시점 기준으로 아직 안 끝난 목표가 있으면 그중 하나를
      // 목표로 삼아 자연스럽게 유도하라고 생성 단계에 알려준다 — 정답을 직접 말해주면 안 됨.
      // 이 목록은 스캔 콜을 기다릴 필요 없이 이미 계산돼있는 값이라 추가 지연이 없다.
      const pendingGoals = activeScenario.microGoals.filter((mg) => !microGoalState.get(mg.id));
      const targetGoal = pendingGoals[0];
      const goalTargetingHint = targetGoal
        ? `\n\n[학습 목표 지침] 아직 아이가 스스로 드러내지 않은 목표: "${targetGoal.description}"
(이게 확인되면 인정하는 근거: ${targetGoal.requiredEvidence.join(", ")})
정답을 직접 말해주지 말고, 아이가 스스로 이 부분을 말하도록 자연스럽게 유도하는 방향으로 반응하거나 되물어라.`
        : `\n\n[학습 목표 지침] 목표를 다 확인했다. 자연스럽게 마무리하는 방향으로 반응하라.`;

      const userInput = `${historyText}\n\n위 대화에서 "친구"의 다음 대사를 만들어라.${profanityCoachingHint}${offTopicRedirectHint}${highConcernHint}${goalTargetingHint}`;

      // 마이크로 목표 스캔은 대사 생성과 서로 결과가 필요 없으니 동시에 돌린다 —
      // "몰래" 판정한다는 §5 취지에도 맞고(자연스러운 대화 흐름을 막지 않음), 병렬이라 시간도 거의 안 더해진다.
      const [microGoalScan, delivered] = await Promise.all([
        scanMicroGoals(provider, pendingGoals, historyText),
        generateApprovedReply(provider, registry.moderation, activeScenario, activePersona, userInput),
      ]);
      const turnMs = Date.now() - turnStart;

      for (const id of microGoalScan.achievedIds) {
        microGoalState.set(id, true);
      }
      const statusLine = activeScenario.microGoals
        .map((mg) => `${mg.id}:${microGoalState.get(mg.id) ? "✓" : "✗"}`)
        .join(" ");
      const newlyAchieved = microGoalScan.achievedIds
        .map((id) => `${id}("${microGoalScan.evidenceById.get(id)}")`)
        .join(", ");
      console.log(
        `  [마이크로목표 스캔 ${microGoalScan.latencyMs}ms] ${statusLine}${newlyAchieved ? ` — 신규 달성: ${newlyAchieved}` : ""}`,
      );

      console.log(`친구> ${delivered.text}${delivered.fallbackUsed ? "  [기본 응답]" : ""}`);
      console.log(`  [전체 소요 ${turnMs}ms(입력 안전검사+마이크로목표 스캔 포함), 후보 ${delivered.candidateAttempts}개 생성]`);
      transcript.push({ speaker: "친구", text: delivered.text });
    }
  } finally {
    rl.close();
  }

  console.log("\n종료.");
}

main().catch((error) => {
  console.error("chat 실행 실패:", error);
  process.exitCode = 1;
});
