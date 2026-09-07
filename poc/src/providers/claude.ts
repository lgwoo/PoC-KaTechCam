import Anthropic from "@anthropic-ai/sdk";
import type { CompletionInput, CompletionResult, Provider } from "./types.js";

const MAX_TOKENS = Number(process.env.MAX_OUTPUT_TOKENS ?? 1024);

export function buildClaudeProvider(): Provider {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  const model = process.env.CLAUDE_MODEL || "claude-opus-5";

  if (!apiKey) {
    return unavailable("ANTHROPIC_API_KEY 미설정");
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
