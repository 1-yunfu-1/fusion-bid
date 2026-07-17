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
