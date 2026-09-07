import { scenario } from "../fixtures/scenario.js";
import type { Registry } from "../providers/registry.js";
import { safeCall, tryParseJson } from "../util.js";
import type { TestResult, TestRow } from "./types.js";

// 주의: 마스코트(강아지) 개입은 기획서에 정식 스펙이 없다.
// "역할극에 대해" 문서 §5 "판정하기 어려운 세부목표는 마스코트가 옆에서 물어보기" 아이디어를
// 우리가 최소 규칙으로 해석해 테스트한다: 세션 종료 시점까지 NOT_OBSERVED로 남은 마이크로 목표가
// 있으면, 그 목표의 required_evidence를 겨냥한 질문을 마스코트가 1개 생성한다.
const ASSUMPTION =
  "마스코트 트리거 규칙(세션 종료 시 미판정 목표당 질문 1개)은 정식 스펙이 아니라 PoC용 가정 구현이다.";

// 역할극 4턴 종료 시점: MG-01/03은 달성, MG-02만 NOT_OBSERVED로 남았다고 가정.
const unresolvedMicroGoal = scenario.microGoals[1]; // MG-02

const MASCOT_GENERATION_PROMPT = `너는 느링고의 마스코트 강아지다. 역할극이 끝났는데 아직 확인하지 못한
세부 학습목표가 있어서, 아이에게 편하게 물어보는 짧은 질문을 하나 만들어야 한다.
목표: ${unresolvedMicroGoal.description}
이 목표가 확인되었다고 볼 수 있는 증거: ${unresolvedMicroGoal.requiredEvidence.join(", ")}
강아지답게 다정하고 짧게, 질문 하나만 만들어라. 텍스트만 답하라(JSON 아님).`;

const JUDGE_SYSTEM_PROMPT = `아래 질문이 다음 "증거"를 아이가 표현하도록 실제로 겨냥하고 있는지 판단하라.
증거: ${unresolvedMicroGoal.requiredEvidence.join(", ")}
JSON으로만 답하라: {"targetsEvidence": boolean, "reason": "한 문장"}`;

interface JudgeOutput {
  targetsEvidence?: boolean;
  reason?: string;
}

export async function run(registry: Registry): Promise<TestResult> {
  const rows: TestRow[] = [];
  const notes: string[] = [ASSUMPTION, `대상 미판정 목표: ${unresolvedMicroGoal.id} (${unresolvedMicroGoal.description})`];

  for (const provider of registry.providers) {
    if (!provider.available) {
      rows.push({
        provider: provider.label || provider.id,
        mascotQuestion: "SKIPPED",
        targetsEvidence: "SKIPPED",
        note: `NO_API_KEY: ${provider.unavailableReason ?? ""}`,
      });
      continue;
    }

    const generationResult = await safeCall(() =>
      provider.complete({ system: MASCOT_GENERATION_PROMPT, user: "질문을 만들어줘." }),
    );

    if (!generationResult.ok) {
      rows.push({
        provider: provider.label,
        mascotQuestion: "ERROR",
        targetsEvidence: "N/A",
        note: generationResult.error,
      });
      continue;
    }

    const mascotQuestion = generationResult.value.text.trim();
    const judgeResult = await safeCall(() =>
      provider.complete({ system: JUDGE_SYSTEM_PROMPT, user: mascotQuestion }),
    );

    let targetsEvidence = "ERROR";
    if (judgeResult.ok) {
      const parsed = tryParseJson<JudgeOutput>(judgeResult.value.text);
      targetsEvidence = parsed?.targetsEvidence === undefined ? "PARSE_ERROR" : String(parsed.targetsEvidence);
    }

    rows.push({
      provider: provider.label,
      mascotQuestion,
      targetsEvidence,
      note: "",
    });
  }

  return {
    id: "03-mascot",
    title: "마스코트(강아지) 개입 — 미판정 세부목표 질문 생성",
    assumption: ASSUMPTION,
    rows,
    notes,
  };
}
