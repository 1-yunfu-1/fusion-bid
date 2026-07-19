export interface TaskOut {
  id: string;
  original_query: string;
  parsed_intent?: Record<string, unknown> | null;
  keywords?: string[] | null;
  regions?: string[] | null;
  start_date?: string | null;
  end_date?: string | null;
  execute_immediately: boolean;
  schedule_enabled: boolean;
  schedule_type?: string | null;
  execute_time?: string | null;
  execute_date?: string | null;
  is_paused?: boolean;
  last_run_at?: string | null;
  next_run_at?: string | null;
  timezone: string;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TaskListResponse {
  items: TaskOut[];
  total: number;
}

export interface TaskExecutionResponse {
  execution_id: string;
  task_id: string;
  status: "success" | "partial" | "failed" | string;
  task_status: string;
  trigger_type: "initial" | "manual" | "scheduled" | string;
  report_scope: "incremental" | "snapshot" | string;
  report_mode: "incremental" | "full_snapshot" | string;
  deduplicate: boolean;
  truncated: boolean;
  next_run_at?: string | null;
  sources_requested: string[];
  sources_succeeded: string[];
  sources_failed: Record<string, string>;
  raw_result_count: number;
  detail_success_count: number;
  detail_metadata_only_count: number;
  detail_failed_count: number;
  detail_human_verification_count: number;
  detail_not_attempted_count: number;
  cached_full_reused_count: number;
  failure_breakdown: Record<string, number>;
  failure_breakdown_by_source: Record<string, Record<string, number>>;
  source_detail_breakdown: Record<string, Record<string, number>>;
  stage_durations_ms: Record<string, number>;
  effective_concurrency: Record<string, unknown>;
  filtered_out_count: number;
  duplicate_count: number;
  cross_source_merge_count: number;
  saved_count: number;
  incremental_count: number;
  update_count: number;
  skipped_already_delivered: number;
  announcement_ids: string[];
  output_items: Array<Record<string, unknown>>;
  dedupe_reasons: string[];
  report_filename?: string | null;
  report_download_url?: string | null;
  analysis_status: string;
  analysis_provider: string;
  analysis_preview: {
    status?: string;
    provider?: string;
    portfolio_summary?: string;
    priority_counts?: Record<string, number>;
    top_projects?: Array<{
      announcement_id?: string;
      title?: string;
      priority?: string;
      deadline_urgency?: string;
    }>;
  };
  error_message?: string | null;
  message: string;
}

export interface TaskExecutionItem {
  id: string;
  status: string;
  trigger_type: string;
  report_scope: string;
  report_mode: string;
  deduplicate: boolean;
  truncated: boolean;
  started_at?: string | null;
  finished_at?: string | null;
  sources_requested: string[];
  sources_succeeded: string[];
  raw_result_count: number;
  filtered_result_count: number;
  duplicate_count: number;
  incremental_count: number;
  detail_full_count: number;
  detail_metadata_count: number;
  detail_failed_count: number;
  detail_human_verification_count: number;
  detail_not_attempted_count: number;
  cached_full_reused_count: number;
  failure_breakdown: Record<string, number>;
  failure_breakdown_by_source: Record<string, Record<string, number>>;
  source_detail_breakdown: Record<string, Record<string, number>>;
  stage_durations_ms: Record<string, number>;
  effective_concurrency: Record<string, unknown>;
  report_filename?: string | null;
  report_download_url?: string | null;
  analysis_status: string;
  analysis_provider: string;
  analysis_preview: TaskExecutionResponse["analysis_preview"];
  error_message?: string | null;
}

export interface TaskExecutionListResponse {
  items: TaskExecutionItem[];
  total: number;
}
