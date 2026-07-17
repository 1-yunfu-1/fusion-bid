export interface LlmStatusResponse {
  prefer_order: string[];
  api: {
    enabled: boolean;
    base_url: string;
    model: string;
    key_configured: boolean;
    key_source?: string;
    key_hint?: string;
    key_message?: string;
    active_profile_id?: string | null;
    active_profile_name?: string | null;
    profile_count?: number;
    timeout: number;
    health: { ok: boolean; message?: string; http_status?: number };
  };
  ollama: {
    enabled: boolean;
    base_url: string;
    model: string;
    timeout: number;
    health: { ok: boolean; message?: string; models?: string[] };
  };
  runtime: {
    path: string;
    current: Record<string, unknown>;
  };
  notes: string[];
}
