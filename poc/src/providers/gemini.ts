import { GoogleGenerativeAI } from "@google/generative-ai";
import { buildOpenAiCompatibleProvider } from "./openaiCompatible.js";
import type { CompletionInput, CompletionResult, Provider } from "./types.js";

const MAX_TOKENS = Number(process.env.MAX_OUTPUT_TOKENS ?? 2048);

export function buildGeminiProvider(): Provider {
  const apiKey = process.env.GEMINI_API_KEY;
  const modelId = process.env.GEMINI_MODEL;
  // 공식 Gemini API가 아닌 제3자 브릿지(예: Timely GPT SDK)를 거치는 옵션.
  // 설정 시 네이티브 @google/generative-ai 대신 OpenAI 호환 chat.completions로 호출한다.
  // ⚠️ PoC 테스트 1회성 용도 — 실제 서비스/실제 아동 데이터에는 사용 금지.
  // 브릿지 사용 시 GEMINI_MODEL은 브릿지 쪽 표기(예: google/gemini-3-flash-preview)로 맞출 것.
  const baseURL = process.env.GEMINI_BASE_URL;

  if (!apiKey) {
    return unavailable("GEMINI_API_KEY 미설정");
  }
  if (!modelId) {
    return unavailable(
      "GEMINI_MODEL 미설정 — .env.example 주석 참고, 현재 사용 가능한 모델 ID를 직접 확인해 채울 것",
    );
  }

  if (baseURL) {
    const label = `Gemini (${modelId}, 브릿지 경유: ${baseURL})`;
    return buildOpenAiCompatibleProvider({ id: "gemini", label, apiKey, model: modelId, baseURL });
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
