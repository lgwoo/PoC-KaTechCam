import { buildClaudeProvider } from "./claude.js";
import { buildGeminiProvider } from "./gemini.js";
import { buildGpt41MiniProvider } from "./gpt41mini.js";
import { buildGptProvider } from "./gpt.js";
import { buildLunaProvider } from "./luna.js";
import { buildMiniProvider } from "./mini.js";
import { buildModerationClient } from "./moderation.js";
import { buildNanoProvider } from "./nano.js";
import type { ModerationClient, Provider } from "./types.js";

export interface Registry {
  providers: Provider[];
  moderation: ModerationClient;
}

export function buildRegistry(): Registry {
  const providers = [
    buildGptProvider(),
    buildGeminiProvider(),
    buildClaudeProvider(),
    buildLunaProvider(),
    buildNanoProvider(),
    buildMiniProvider(),
    buildGpt41MiniProvider(),
  ];
  const moderation = buildModerationClient();
  return { providers, moderation };
}

export function availableProviders(registry: Registry): Provider[] {
  return registry.providers.filter((provider) => provider.available);
}
