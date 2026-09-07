import OpenAI from "openai";
import type { CompletionInput, CompletionResult, Provider } from "./types.js";

const MAX_TOKENS = Number(process.env.MAX_OUTPUT_TOKENS ?? 1024);

export function buildGptProvider(): Provider {
  const apiKey = process.env.OPENAI_API_KEY;
  const model = process.env.OPENAI_MODEL;

  if (!apiKey) {
    return unavailable("OPENAI_API_KEY 미설정");
  }
  if (!model) {
    return unavailable(
      "OPENAI_MODEL 미설정 — .env.example 주석 참고, 현재 사용 가능한 모델 ID를 직접 확인해 채울 것",
    );
  }

  const client = new OpenAI({ apiKey });

  return {
    id: "gpt",
    label: `GPT (${model})`,
    available: true,
    async complete({ system, user }: CompletionInput): Promise<CompletionResult> {
      const start = Date.now();
      const response = await client.chat.completions.create({
        model,
        max_tokens: MAX_TOKENS,
        messages: [
          ...(system ? [{ role: "system" as const, content: system }] : []),
          { role: "user" as const, content: user },
        ],
      });
      const latencyMs = Date.now() - start;
      const text = response.choices[0]?.message?.content ?? "";
      return { text, latencyMs };
    },
  };
}

function unavailable(reason: string): Provider {
  return {
    id: "gpt",
    label: "GPT",
    available: false,
    unavailableReason: reason,
    async complete() {
      throw new Error(`GPT provider unavailable: ${reason}`);
    },
  };
}
