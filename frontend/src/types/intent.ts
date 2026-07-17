export interface DateRange {
  start_date: string | null;
  end_date: string | null;
  original_expression: string | null;
}

export interface Schedule {
  enabled: boolean;
  schedule_type: "once" | "daily" | "weekly" | "monthly" | null;
  execute_date: string | null;
  execute_time: string | null;
  timezone: string;
}

export interface ParsedIntent {
  original_query: string;
  keywords: string[];
  exclude_keywords: string[];
  regions: string[];
  date_range: DateRange;
  schedule: Schedule;
  execute_immediately: boolean;
}

export interface ValidationIssue {
  code: string;
  message: string;
  field?: string | null;
  severity: "error" | "warning";
}

export interface ParseResponse {
  intent: ParsedIntent;
  parser_used: string;
  llm_attempted: boolean;
  llm_success: boolean;
  llm_error: string | null;
  issues: ValidationIssue[];
  needs_user_input: boolean;
  can_confirm: boolean;
  suggestions: string[];
  warnings: string[];
}

export interface ConfirmParseResponse {
  task_id: string;
  status: string;
  intent: ParsedIntent;
  issues: ValidationIssue[];
  message: string;
}
