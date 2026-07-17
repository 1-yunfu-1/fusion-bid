import { apiClient } from "./client";
import type { ParsedIntent, ValidationIssue } from "../types/intent";
import type { TaskListResponse, TaskOut } from "../types/task";

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
