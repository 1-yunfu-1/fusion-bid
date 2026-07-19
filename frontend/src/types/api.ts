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
    pool_size?: number;
    active_workers?: number;
    queue_size?: number;
    adaptive_mode?: boolean;
    pdf_pipeline?: {
      memory_pdf_bytes?: boolean;
      text_parser?: boolean;
      rasterizer?: boolean;
      ocr_engine?: boolean;
      text_ready?: boolean;
      scanned_pdf_ready?: boolean;
      parse_concurrency?: number;
      ocr_concurrency?: number;
    };
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
