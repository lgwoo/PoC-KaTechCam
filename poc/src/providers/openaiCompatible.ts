import OpenAI from "openai";
import type { CompletionInput, CompletionResult, Provider, ProviderId } from "./types.js";

const MAX_TOKENS = Number(process.env.MAX_OUTPUT_TOKENS ?? 2048);

interface BuildOpenAiCompatibleOptions {
  id: ProviderId;
  label: string;
  apiKey: string;
  model: string;
  baseURL?: string;
}

/**
 * chat.completions 인터페이스로 호출하는 공통 factory.
 * baseURL을 넘기면 OpenAI 호환 제3자 브릿지(예: Timely GPT SDK)로 라우팅된다 —
 * 이 경우 호출부에서 label에 "브릿지 경유"를 직접 표기해야 한다.
 * ⚠️ 브릿지 경유는 PoC 테스트 1회성 용도 — 실제 서비스/실제 아동 데이터에는 쓰지 말 것.
 */
export function buildOpenAiCompatibleProvider({
  id,
  label,
  apiKey,
  model,
  baseURL,
}: BuildOpenAiCompatibleOptions): Provider {
  const client = new OpenAI({ apiKey, ...(baseURL ? { baseURL } : {}) });

  return {
    id,
    label,
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
