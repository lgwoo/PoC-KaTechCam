import type { Registry } from "../providers/registry.js";
import { safeCall, tryParseJson } from "../util.js";
import type { TestResult, TestRow } from "./types.js";

// LVL-07 (근거 기록), 기능요구사항 §14 micro_goal_performance 구조를 본뜬 mock 데이터.
// 기획서 "일정" 섹션의 MVP 방침: "추천은 에이전트X, 코드 상으로 처리"이므로,
// 여기서는 그 방침과 별개로 "에이전트로 생성했을 때 품질이 어떤지"를 참고용으로 확인한다.
const mockSummary = {
  sessionId: "SESSION-001",
  scenarioLevel: "L1",
  microGoalResults: [
    { microGoalId: "MG-01", turnsToAchievement: 1, finalSupportLevel: "S0", achievedWithoutAnswerReveal: true },
    { microGoalId: "MG-02", turnsToAchievement: 3, finalSupportLevel: "S2", achievedWithoutAnswerReveal: true },
    { microGoalId: "MG-03", turnsToAchievement: 4, finalSupportLevel: "S3", achievedWithoutAnswerReveal: false },
  ],
};

const SYSTEM_PROMPT = `너는 느링고의 강사 리포트·추천 목표 생성기다. 아래 역할극 수행 요약을 바탕으로
강사에게 보여줄 리포트와 다음 학습 목표 추천을 만들어라.
입력 데이터: ${JSON.stringify(mockSummary)}
반드시 아래 JSON 형식으로만 답하라:
{
  "report_summary": "강사가 읽을 한두 문장 요약",
  "per_micro_goal_notes": [{"micro_goal_id": string, "note": string}],
  "next_goal_recommendation": "다음 학습 목표 제안 한 문장",
  "recommendation_rationale": "위 요약 데이터의 구체적 수치(턴 수, 지원 수준, 정답 제공 여부 등)를 근거로 든 설명"
}`;

interface ReportOutput {
  report_summary?: string;
  per_micro_goal_notes?: unknown[];
  next_goal_recommendation?: string;
  recommendation_rationale?: string;
}

const REQUIRED_FIELDS: (keyof ReportOutput)[] = [
  "report_summary",
  "per_micro_goal_notes",
  "next_goal_recommendation",
  "recommendation_rationale",
];

// 근거 데이터에 있는 값 중 일부가 rationale 텍스트에 실제로 언급되는지 대충 확인하는 grounding 체크.
const GROUNDING_KEYWORDS = ["S3", "MG-03", "4턴", "정답"];

export async function run(registry: Registry): Promise<TestResult> {
  const rows: TestRow[] = [];
  const notes: string[] = [
    'MVP 방침("추천은 에이전트 없이 코드로 처리")과 별개로, 에이전트 생성 방식의 품질을 참고용으로 확인.',
  ];

  for (const provider of registry.providers) {
    if (!provider.available) {
      rows.push({
        provider: provider.label || provider.id,
        missingFields: "SKIPPED",
        grounded: "SKIPPED",
        note: `NO_API_KEY: ${provider.unavailableReason ?? ""}`,
      });
      continue;
    }

    const result = await safeCall(() =>
      provider.complete({ system: SYSTEM_PROMPT, user: "리포트와 추천을 생성해줘." }),
    );

    if (!result.ok) {
      rows.push({ provider: provider.label, missingFields: "ERROR", grounded: "N/A", note: result.error });
      continue;
    }

    const parsed = tryParseJson<ReportOutput>(result.value.text);
    if (!parsed) {
      rows.push({ provider: provider.label, missingFields: "PARSE_ERROR", grounded: "N/A", note: result.value.text.slice(0, 200) });
      continue;
    }

    const missing = REQUIRED_FIELDS.filter((field) => parsed[field] === undefined);
    const rationale = parsed.recommendation_rationale ?? "";
    const groundedHits = GROUNDING_KEYWORDS.filter((keyword) => rationale.includes(keyword));

    rows.push({
      provider: provider.label,
      missingFields: missing.length === 0 ? "없음" : missing.join(", "),
      grounded: `${groundedHits.length}/${GROUNDING_KEYWORDS.length} 근거 키워드 언급`,
      note: rationale.slice(0, 120),
    });
  }

  return {
    id: "05-recommend-report",
    title: "추천 목표 · 강사 리포트 생성",
    rows,
    notes,
  };
}
