import { scenario } from "../fixtures/scenario.js";
import type { Registry } from "../providers/registry.js";
import { mean, percentile, safeCall } from "../util.js";
import type { TestResult, TestRow } from "./types.js";

const SAMPLES = Number(process.env.LATENCY_SAMPLES ?? 5);
const CHILD_UTTERANCE = "친구가 울고 있어.";

// 응답 시간 문서 PERF-03(생성 p95 3초 이하)와 대조하기 위한 참고값.
// STT/TTS 없이 순수 LLM 2단계(원인 판단 + 응답 생성)만 측정한다.
const PERF_03_TARGET_MS = 3000;

const ANALYSIS_SYSTEM_PROMPT = `너는 느링고의 원인 판단 에이전트다. 아래 시나리오와 아동 발화를 보고
학습 상태(GOAL_ACHIEVED/CORRECT_BUT_SHALLOW/PARTIAL_UNDERSTANDING/MISCONCEPTION/OFF_TOPIC/HELP_NEEDED/NOT_ASSESSABLE) 중 하나와
대표 부족 원인을 JSON으로만 답하라: {"learning_state": string, "primary_gap": string}
시나리오 사실: ${scenario.scenarioFacts.join(" ")}
현재 마이크로 목표: ${scenario.microGoals[1].description}`;

const GENERATION_SYSTEM_PROMPT = `너는 느링고의 응답 생성 에이전트다. 한 번에 하나의 질문만 담은,
초급 수준의 짧은 유도 질문 하나를 생성하라. 텍스트만 답하라(JSON 아님).`;

export async function run(registry: Registry): Promise<TestResult> {
  const rows: TestRow[] = [];
  const notes: string[] = [
    `provider별 ${SAMPLES}회 반복 측정. PERF-03 목표치(생성 단계 p95 ${PERF_03_TARGET_MS}ms) 대조 참고용 — STT/TTS는 제외한 LLM 2단계만 측정.`,
  ];

  for (const provider of registry.providers) {
    if (!provider.available) {
      rows.push({
        provider: provider.label || provider.id,
        stage: "-",
        samples: 0,
        meanMs: "SKIPPED",
        p50Ms: "SKIPPED",
        p95Ms: "SKIPPED",
        note: `NO_API_KEY: ${provider.unavailableReason ?? ""}`,
      });
      continue;
    }

    const analysisLatencies: number[] = [];
    const generationLatencies: number[] = [];
    let errorCount = 0;

    for (let i = 0; i < SAMPLES; i++) {
      const analysisResult = await safeCall(() =>
        provider.complete({ system: ANALYSIS_SYSTEM_PROMPT, user: CHILD_UTTERANCE }),
      );
      if (analysisResult.ok) {
        analysisLatencies.push(analysisResult.value.latencyMs);
      } else {
        errorCount++;
      }

      const generationResult = await safeCall(() =>
        provider.complete({ system: GENERATION_SYSTEM_PROMPT, user: CHILD_UTTERANCE }),
      );
      if (generationResult.ok) {
        generationLatencies.push(generationResult.value.latencyMs);
      } else {
        errorCount++;
      }
    }

    rows.push({
      provider: provider.label,
      stage: "analysis (원인 판단)",
      samples: analysisLatencies.length,
      meanMs: Math.round(mean(analysisLatencies)),
      p50Ms: Math.round(percentile(analysisLatencies, 50)),
      p95Ms: Math.round(percentile(analysisLatencies, 95)),
      note: errorCount > 0 ? `${errorCount}건 호출 실패` : "",
    });
    rows.push({
      provider: provider.label,
      stage: "generation (응답 생성)",
      samples: generationLatencies.length,
      meanMs: Math.round(mean(generationLatencies)),
      p50Ms: Math.round(percentile(generationLatencies, 50)),
      p95Ms: Math.round(percentile(generationLatencies, 95)),
      note:
        percentile(generationLatencies, 95) > PERF_03_TARGET_MS
          ? `PERF-03 목표(${PERF_03_TARGET_MS}ms) 초과`
          : "PERF-03 목표 이내",
    });
  }

  return {
    id: "02-latency",
    title: "생성 시간 (provider별 원인 판단 + 응답 생성 latency)",
    rows,
    notes,
  };
}
