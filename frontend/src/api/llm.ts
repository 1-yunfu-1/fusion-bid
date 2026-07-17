import { apiClient } from "./client";
import type { LlmStatusResponse } from "../types/llm";

export async function fetchLlmStatus(): Promise<LlmStatusResponse> {
  const { data } = await apiClient.get<LlmStatusResponse>("/api/llm/status");
  return data;
}

export async function updateLlmRuntime(body: Record<string, unknown>) {
  const { data } = await apiClient.put("/api/llm/runtime", body);
  return data;
}

export type ApiModelsResponse = {
  ok: boolean;
  message?: string;
  base_url?: string;
  selected?: string;
  count?: number;
  models: { id: string; owned_by?: string; created?: number }[];
};

export async function fetchApiModels(): Promise<ApiModelsResponse> {
  const { data } = await apiClient.get<ApiModelsResponse>("/api/llm/models", {
    timeout: 30000,
  });
  return data;
}

export async function probeApiModels(baseUrl?: string): Promise<ApiModelsResponse> {
  const { data } = await apiClient.post<ApiModelsResponse>(
    "/api/llm/models/probe",
    { base_url: baseUrl || null },
    { timeout: 30000 },
  );
  return data;
}

export async function selectApiModel(model: string) {
  const { data } = await apiClient.post("/api/llm/models/select", { model });
  return data as { ok: boolean; api_model: string; message?: string };
}

export type CredentialsStatus = {
  ok?: boolean;
  configured: boolean;
  source?: string;
  hint?: string;
  message?: string;
  secrets_path?: string;
  action?: string;
  active_profile_id?: string | null;
  active_profile_name?: string | null;
  profile_count?: number;
  profiles?: ApiProfile[];
  count?: number;
};

export type ApiProfile = {
  id: string;
  name: string;
  base_url: string;
  model?: string;
  key_configured: boolean;
  key_hint?: string;
  is_active?: boolean;
  created_at?: string;
  updated_at?: string;
};

export type ApiProfileInput = {
  name: string;
  base_url?: string;
  api_key?: string | null;
  model?: string | null;
  activate?: boolean;
  clear_key?: boolean;
};

export async function fetchCredentialsStatus(): Promise<CredentialsStatus> {
  const { data } = await apiClient.get<CredentialsStatus>("/api/llm/credentials");
  return data;
}

export async function saveApiKey(apiKey: string): Promise<CredentialsStatus> {
  const { data } = await apiClient.put<CredentialsStatus>("/api/llm/credentials", {
    api_key: apiKey,
  });
  return data;
}

export async function clearApiKey(): Promise<CredentialsStatus> {
  const { data } = await apiClient.put<CredentialsStatus>("/api/llm/credentials", {
    clear: true,
  });
  return data;
}

export async function fetchApiProfiles(): Promise<CredentialsStatus> {
  const { data } = await apiClient.get<CredentialsStatus>("/api/llm/profiles");
  return data;
}

export async function createApiProfile(body: ApiProfileInput) {
  const { data } = await apiClient.post("/api/llm/profiles", body);
  return data as CredentialsStatus & { profile: ApiProfile; message?: string };
}

export async function updateApiProfile(id: string, body: ApiProfileInput) {
  const { data } = await apiClient.put(`/api/llm/profiles/${id}`, body);
  return data as CredentialsStatus & { profile: ApiProfile; message?: string };
}

export async function activateApiProfile(id: string) {
  const { data } = await apiClient.post(`/api/llm/profiles/${id}/activate`);
  return data as CredentialsStatus & { profile: ApiProfile; message?: string };
}

export async function deleteApiProfile(id: string) {
  const { data } = await apiClient.delete(`/api/llm/profiles/${id}`);
  return data as CredentialsStatus & { message?: string };
}

export async function fetchOllamaModels() {
  const { data } = await apiClient.get("/api/llm/ollama/models");
  return data as {
    ok: boolean;
    message?: string;
    models: { name: string; size?: number }[];
    selected?: string;
    recommended?: { name: string; note: string }[];
  };
}

export async function pullOllamaModel(model: string) {
  const { data } = await apiClient.post(
    "/api/llm/ollama/pull",
    { model },
    { timeout: 600000 },
  );
  return data as { ok: boolean; model: string; status: string };
}

export async function selectOllamaModel(model: string) {
  const { data } = await apiClient.post("/api/llm/ollama/select", { model });
  return data;
}
