import OpenAI from "openai";
import type { ModerationClient, ModerationResult } from "./types.js";

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
      return { flagged: result?.flagged ?? false, categories, latencyMs };
    },
  };
}
