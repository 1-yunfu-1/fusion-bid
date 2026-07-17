import { apiClient } from "./client";
import type { ConfirmParseResponse, ParseResponse, ParsedIntent } from "../types/intent";

export async function parseQuery(payload: {
  query: string;
  prefer_llm?: boolean | null;
  reference_time?: string | null;
}): Promise<ParseResponse> {
  const { data } = await apiClient.post<ParseResponse>("/api/parse", payload);
  return data;
}

export async function confirmParse(payload: {
  intent: ParsedIntent;
  force?: boolean;
}): Promise<ConfirmParseResponse> {
  const { data } = await apiClient.post<ConfirmParseResponse>("/api/parse/confirm", payload);
  return data;
}
