import { apiClient } from "./client";
import type { ConfirmParseResponse, ParseResponse, ParsedIntent } from "../types/intent";

export async function parseQuery(payload: {
  query: string;
  prefer_llm?: boolean | null;
  reference_time?: string | null;
}): Promise<ParseResponse> {
  // LLM parsing may legitimately take longer than the shared 15-second API
  // timeout. The backend still owns rule-based fallback, so leave enough time
  // for that response instead of presenting a successful parse as a failure.
  const { data } = await apiClient.post<ParseResponse>("/api/parse", payload, {
    timeout: 60_000,
  });
  return data;
}

export async function confirmParse(payload: {
  intent: ParsedIntent;
  force?: boolean;
}): Promise<ConfirmParseResponse> {
  const { data } = await apiClient.post<ConfirmParseResponse>("/api/parse/confirm", payload);
  return data;
}
