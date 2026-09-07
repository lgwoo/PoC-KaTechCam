import OpenAI from "openai";
import type { ModerationClient, ModerationResult } from "./types.js";

export function buildModerationClient(): ModerationClient {
  const apiKey = process.env.OPENAI_API_KEY;
  const model = process.env.OPENAI_MODERATION_MODEL || "omni-moderation-latest";

  if (!apiKey) {
    return {
      available: false,
      unavailableReason: "OPENAI_API_KEY 미설정 (Moderation도 동일 키 사용)",
      async moderate() {
        throw new Error("Moderation client unavailable: OPENAI_API_KEY 미설정");
      },
    };
  }

  const client = new OpenAI({ apiKey });

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
