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
