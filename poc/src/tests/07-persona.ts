import { scenario } from "../fixtures/scenario.js";
import { friendPersona } from "../fixtures/persona.js";
import type { Registry } from "../providers/registry.js";
import { safeCall, tryParseJson } from "../util.js";
import type { TestResult, TestRow } from "./types.js";

// 주의: 캐릭터 성격·말투는 정식 스펙이 없다. "역할극에 대해" 문서의 우려("AI가 캐릭터가 되어
// 말하는 게 아니라 상황을 설명하는 선생님처럼 되어 있다")를 반영해, 우리가 정의한 persona.ts로
// 실제로 1인칭 캐릭터 톤을 유지할 수 있는지 확인한다.
const ASSUMPTION = "캐릭터 성격·말투(persona.ts)는 정식 스펙이 아니라 PoC용 가정이다.";

function buildGenerationPrompt(): string {
  return `너는 역할극 캐릭터 "${friendPersona.name}"이다.
성격: ${friendPersona.personality}
말투: ${friendPersona.speechStyle}
규칙: ${friendPersona.firstPersonRule}
시나리오 사실: ${scenario.scenarioFacts.join(" ")}
지금 막 넘어져서 아픈 상황이다. 아이의 말에 캐릭터로서 짧게 반응하라. 대사만 출력하라(따옴표·설명 없이).`;
}

const JUDGE_SYSTEM_PROMPT = `아래 대사가 다음 조건을 지키는지 판단하라.
성격: ${friendPersona.personality}
말투: ${friendPersona.speechStyle}
규칙: ${friendPersona.firstPersonRule}
JSON으로만 답하라: {"firstPerson": boolean, "styleMatch": boolean, "reason": "한 문장"}`;

interface JudgeOutput {
  firstPerson?: boolean;
  styleMatch?: boolean;
}

export async function run(registry: Registry): Promise<TestResult> {
  const rows: TestRow[] = [];
  const notes: string[] = [ASSUMPTION];

  for (const provider of registry.providers) {
    if (!provider.available) {
      rows.push({
        provider: provider.label || provider.id,
        line: "SKIPPED",
        firstPerson: "SKIPPED",
        styleMatch: "SKIPPED",
        note: `NO_API_KEY: ${provider.unavailableReason ?? ""}`,
      });
      continue;
    }

    const generationResult = await safeCall(() =>
      provider.complete({ system: buildGenerationPrompt(), user: "너 왜 그래? 많이 아파?" }),
    );

    if (!generationResult.ok) {
      rows.push({ provider: provider.label, line: "ERROR", firstPerson: "N/A", styleMatch: "N/A", note: generationResult.error });
      continue;
    }

    const line = generationResult.value.text.trim();
    const judgeResult = await safeCall(() => provider.complete({ system: JUDGE_SYSTEM_PROMPT, user: line }));

    let firstPerson = "ERROR";
    let styleMatch = "ERROR";
    if (judgeResult.ok) {
      const parsed = tryParseJson<JudgeOutput>(judgeResult.value.text);
      firstPerson = parsed?.firstPerson === undefined ? "PARSE_ERROR" : String(parsed.firstPerson);
      styleMatch = parsed?.styleMatch === undefined ? "PARSE_ERROR" : String(parsed.styleMatch);
    }

    rows.push({ provider: provider.label, line, firstPerson, styleMatch, note: "" });
  }

  return {
    id: "07-persona",
    title: "상황·캐릭터에 맞는 대사 생성",
    assumption: ASSUMPTION,
    rows,
    notes,
  };
}
