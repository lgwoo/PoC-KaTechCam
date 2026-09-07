import { scenario } from "../fixtures/scenario.js";
import { microGoalSamples } from "../fixtures/turns.js";
import type { Registry } from "../providers/registry.js";
import { safeCall, tryParseJson } from "../util.js";
import type { TestResult, TestRow } from "./types.js";

// ANA-01~09, 기능요구사항 §14.4 학습 상태 코드 재현.
const VALID_STATES = [
  "GOAL_ACHIEVED",
  "CORRECT_BUT_SHALLOW",
  "PARTIAL_UNDERSTANDING",
  "MISCONCEPTION",
  "OFF_TOPIC",
  "HELP_NEEDED",
  "NOT_ASSESSABLE",
];

interface JudgeOutput {
  learning_state?: string;
}

function buildSystemPrompt(microGoalDescription: string, requiredEvidence: string[]): string {
  return `너는 느링고의 원인 판단 에이전트다. 아래 시나리오와 마이크로 목표를 기준으로
아동 발화 하나의 학습 상태를 판단하라.
시나리오 사실: ${scenario.scenarioFacts.join(" ")}
현재 마이크로 목표: ${microGoalDescription}
목표 달성 증거: ${requiredEvidence.join(", ")}
가능한 학습 상태 값: ${VALID_STATES.join(", ")}
반드시 이 중 하나만 골라 JSON으로만 답하라: {"learning_state": string}`;
}

export async function run(registry: Registry): Promise<TestResult> {
  const rows: TestRow[] = [];
  const notes: string[] = [];
  const microGoalById = new Map(scenario.microGoals.map((mg) => [mg.id, mg]));

  for (const provider of registry.providers) {
    if (!provider.available) {
      rows.push({
        provider: provider.label || provider.id,
        accuracy: "SKIPPED",
        correct: 0,
        total: microGoalSamples.length,
        note: `NO_API_KEY: ${provider.unavailableReason ?? ""}`,
      });
      continue;
    }

    let correct = 0;
    const perCaseRows: TestRow[] = [];

    for (const sample of microGoalSamples) {
      const microGoal = microGoalById.get(sample.microGoalId)!;
      const systemPrompt = buildSystemPrompt(microGoal.description, microGoal.requiredEvidence);
      const result = await safeCall(() => provider.complete({ system: systemPrompt, user: sample.text }));

      let predicted = "ERROR";
      if (result.ok) {
        const parsed = tryParseJson<JudgeOutput>(result.value.text);
        predicted = parsed?.learning_state ?? "PARSE_ERROR";
      }
      const isCorrect = predicted === sample.expectedLearningState;
      if (isCorrect) correct++;

      perCaseRows.push({
        provider: provider.label,
        sample: sample.id,
        expected: sample.expectedLearningState,
        predicted,
        correct: isCorrect,
        note: sample.note,
      });
    }

    rows.push(
      {
        provider: provider.label,
        sample: "TOTAL",
        expected: "-",
        predicted: "-",
        correct: `${correct}/${microGoalSamples.length}`,
        note: `정확도 ${((correct / microGoalSamples.length) * 100).toFixed(0)}%`,
      },
      ...perCaseRows,
    );
  }

  return {
    id: "04-microgoal",
    title: "세부목표(micro goal) 판정 정확도",
    rows,
    notes,
  };
}
