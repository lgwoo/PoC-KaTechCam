import OpenAI from "openai";
import type { ModerationClient, ModerationResult } from "./types.js";

export type ModerationSeverity = "CRITICAL" | "HIGH" | "MODERATE" | "NONE";

// 카테고리별 대응 방안 — SAFE-04 "즉각적인 위험 가능성은 일반 역할극보다 보호 절차를 우선" 재현.
// CRITICAL: 아동 입력이든 AI 출력이든, 우리 자체 분류기가 뭐라 했든 상관없이 즉시 RISK/차단 강제.
// HIGH: 구체적 위협·그래픽 폭력 — AI 출력은 차단+재생성, 아동 입력은 마스코트 개입+화제전환.
// MODERATE: 기존처럼 순화·코칭만 하고 역할극은 안 멈춤.
const CRITICAL_CATEGORIES = new Set(["self-harm", "self-harm/intent", "self-harm/instructions", "sexual/minors"]);
const HIGH_CATEGORIES = new Set([
  "harassment/threatening",
  "hate/threatening",
  "illicit/violent",
  "violence/graphic",
  "sexual", // 아동 대상 서비스 특성상 일반 MODERATE보다 격상
]);

export function moderationSeverity(categories: string[]): ModerationSeverity {
  if (categories.some((c) => CRITICAL_CATEGORIES.has(c))) return "CRITICAL";
  if (categories.some((c) => HIGH_CATEGORIES.has(c))) return "HIGH";
  if (categories.length > 0) return "MODERATE";
  return "NONE";
}

export function buildModerationClient(): ModerationClient {
  // OPENAI_API_KEY는 GPT provider가 브릿지(Timely 등) 키로 쓸 수도 있어서 공유하면 충돌한다.
  // Moderation은 항상 진짜 OpenAI 키가 필요하므로 별도 변수를 우선 사용한다.
  const apiKey = process.env.OPENAI_MODERATION_API_KEY || process.env.OPENAI_API_KEY;
  const model = process.env.OPENAI_MODERATION_MODEL || "omni-moderation-latest";

  if (!apiKey) {
    return {
      available: false,
      unavailableReason: "OPENAI_MODERATION_API_KEY(또는 OPENAI_API_KEY) 미설정",
      async moderate() {
        throw new Error("Moderation client unavailable: API 키 미설정");
      },
    };
  }

  // 주의: openai SDK는 baseURL을 명시하지 않으면 process.env.OPENAI_BASE_URL을 자동으로 읽는다.
  // GPT provider가 브릿지(OPENAI_BASE_URL)를 쓰도록 설정되어 있어도 Moderation은 항상
  // 공식 OpenAI 엔드포인트로만 호출해야 하므로 baseURL을 여기서 명시적으로 고정한다.
  const client = new OpenAI({ apiKey, baseURL: "https://api.openai.com/v1" });

  return {
    available: true,
    async moderate(text: string): Promise<ModerationResult> {
      const start = Date.now();
      const response = await client.moderations.create({ model, input: text });
      const latencyMs = Date.now() - start;
      const result = response.results[0];
      const categories = Object.entries(result?.categories ?? {})
        .filter(([, flagged]) => flagged)
        .map(([category]) => category);
      const rawScores = result?.category_scores as unknown as Record<string, number> | undefined;
      const categoryScores = Object.fromEntries(
        categories.map((category) => [category, rawScores?.[category] ?? 0]),
      );
      return { flagged: result?.flagged ?? false, categories, categoryScores, latencyMs };
    },
  };
}
