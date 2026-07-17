import { apiClient } from "./client";
import type { HealthResponse, MetaResponse } from "../types/api";

export async function fetchHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>("/api/health");
  return data;
}

export async function fetchMeta(): Promise<MetaResponse> {
  const { data } = await apiClient.get<MetaResponse>("/api/meta");
  return data;
}
