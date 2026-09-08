import Anthropic from "@anthropic-ai/sdk";
import { buildOpenAiCompatibleProvider } from "./openaiCompatible.js";
import type { CompletionInput, CompletionResult, Provider } from "./types.js";

const MAX_TOKENS = Number(process.env.MAX_OUTPUT_TOKENS ?? 2048);

export function buildClaudeProvider(): Provider {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  const model = process.env.CLAUDE_MODEL || "claude-opus-5";
  // 공식 Anthropic API가 아닌 제3자 브릿지(예: Timely GPT SDK)를 거치는 옵션.
  // 설정 시 네이티브 @anthropic-ai/sdk 대신 OpenAI 호환 chat.completions로 호출한다.
  // ⚠️ PoC 테스트 1회성 용도 — 실제 서비스/실제 아동 데이터에는 사용 금지.
  // 브릿지 사용 시 CLAUDE_MODEL은 브릿지 쪽 표기(예: anthropic/claude-sonnet-4.6)로 맞출 것.
  const baseURL = process.env.CLAUDE_BASE_URL;

  if (!apiKey) {
    return unavailable("ANTHROPIC_API_KEY 미설정");
  }

  if (baseURL) {
    const label = `Claude (${model}, 브릿지 경유: ${baseURL})`;
    return buildOpenAiCompatibleProvider({ id: "claude", label, apiKey, model, baseURL });
  }

  const client = new Anthropic({ apiKey });

  return {
    id: "claude",
    label: `Claude (${model})`,
    available: true,
    async complete({ system, user }: CompletionInput): Promise<CompletionResult> {
      const start = Date.now();
      const response = await client.messages.create({
        model,
        max_tokens: MAX_TOKENS,
        ...(system ? { system } : {}),
        messages: [{ role: "user", content: user }],
      });
      const latencyMs = Date.now() - start;
      const textBlock = response.content.find(
        (block): block is Anthropic.TextBlock => block.type === "text",
      );
      return { text: textBlock?.text ?? "", latencyMs };
    },
  };
}

function unavailable(reason: string): Provider {
  return {
    id: "claude",
    label: "Claude",
    available: false,
    unavailableReason: reason,
    async complete() {
      throw new Error(`Claude provider unavailable: ${reason}`);
    },
  };
}
