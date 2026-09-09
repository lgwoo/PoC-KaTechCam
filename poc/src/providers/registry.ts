import { buildClaudeProvider } from "./claude.js";
import { buildGeminiProvider } from "./gemini.js";
import { buildGptProvider } from "./gpt.js";
import { buildLunaProvider } from "./luna.js";
import { buildModerationClient } from "./moderation.js";
import type { ModerationClient, Provider } from "./types.js";

export interface Registry {
  providers: Provider[];
  moderation: ModerationClient;
}

export function buildRegistry(): Registry {
  const providers = [buildGptProvider(), buildGeminiProvider(), buildClaudeProvider(), buildLunaProvider()];
  const moderation = buildModerationClient();
  return { providers, moderation };
}

export function availableProviders(registry: Registry): Provider[] {
  return registry.providers.filter((provider) => provider.available);
}
