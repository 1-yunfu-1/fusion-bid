export interface HealthResponse {
  status: string;
  app: string;
  version: string;
  phase: string;
  timezone: string;
  time: string;
  database: string;
  database_ok: boolean;
  database_revision?: string;
  extraction_version?: string;
  capabilities?: string[];
  public_browser?: {
    state: "not_started" | "starting" | "ready" | "busy" | "needs_verification" | "unavailable";
    engine?: string | null;
    profile_ready: boolean;
    last_error?: string | null;
  };
  message: string;
}

export interface MetaResponse {
  name: string;
  version: string;
  phase: string;
  timezone: string;
  language: string;
  description: string;
  features_ready: string[];
  features_planned: string[];
}
