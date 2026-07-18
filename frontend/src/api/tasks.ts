import { apiClient } from "./client";
import type { ParsedIntent, ValidationIssue } from "../types/intent";
import type {
  TaskExecutionListResponse,
  TaskExecutionResponse,
  TaskListResponse,
  TaskOut,
} from "../types/task";

export async function listTasks(): Promise<TaskListResponse> {
  const { data } = await apiClient.get<TaskListResponse>("/api/tasks");
  return data;
}

export async function getTask(id: string): Promise<TaskOut> {
  const { data } = await apiClient.get<TaskOut>(`/api/tasks/${id}`);
  return data;
}

export type TaskUpdateResponse = {
  task: TaskOut;
  issues: ValidationIssue[];
  message: string;
};

export async function updateTask(
  id: string,
  intent: ParsedIntent,
  force = false,
): Promise<TaskUpdateResponse> {
  const { data } = await apiClient.put<TaskUpdateResponse>(`/api/tasks/${id}`, {
    intent,
    force,
  });
  return data;
}

export async function executeTask(
  id: string,
  triggerType: "initial" | "manual" = "manual",
  reportMode: "incremental" | "full_snapshot" = "incremental",
): Promise<TaskExecutionResponse> {
  const { data } = await apiClient.post<TaskExecutionResponse>(
    `/api/tasks/${id}/execute`,
    { trigger_type: triggerType, report_mode: reportMode },
    { timeout: 300000 },
  );
  return data;
}

export async function listTaskExecutions(id: string): Promise<TaskExecutionListResponse> {
  const { data } = await apiClient.get<TaskExecutionListResponse>(
    `/api/tasks/${id}/executions`,
  );
  return data;
}
