import { GoogleGenerativeAI } from "@google/generative-ai";
import type { CompletionInput, CompletionResult, Provider } from "./types.js";

const MAX_TOKENS = Number(process.env.MAX_OUTPUT_TOKENS ?? 1024);

export function buildGeminiProvider(): Provider {
  const apiKey = process.env.GEMINI_API_KEY;
  const modelId = process.env.GEMINI_MODEL;

  if (!apiKey) {
    return unavailable("GEMINI_API_KEY 미설정");
  }
  if (!modelId) {
    return unavailable(
      "GEMINI_MODEL 미설정 — .env.example 주석 참고, 현재 사용 가능한 모델 ID를 직접 확인해 채울 것",
    );
  }

  const genAI = new GoogleGenerativeAI(apiKey);

  return {
    id: "gemini",
    label: `Gemini (${modelId})`,
    available: true,
    async complete({ system, user }: CompletionInput): Promise<CompletionResult> {
      const model = genAI.getGenerativeModel({
        model: modelId,
        systemInstruction: system,
        generationConfig: { maxOutputTokens: MAX_TOKENS },
      });
      const start = Date.now();
      const result = await model.generateContent(user);
      const latencyMs = Date.now() - start;
      const text = result.response.text();
      return { text, latencyMs };
    },
  };
}

function unavailable(reason: string): Provider {
  return {
    id: "gemini",
    label: "Gemini",
    available: false,
    unavailableReason: reason,
    async complete() {
      throw new Error(`Gemini provider unavailable: ${reason}`);
    },
  };
}
