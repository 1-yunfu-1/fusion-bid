import axios from "axios";

const baseURL = import.meta.env.VITE_API_BASE_URL || "";

export const apiClient = axios.create({
  baseURL,
  timeout: 15000,
  headers: {
    Accept: "application/json",
  },
});

export function apiResourceUrl(path: string): string {
  const base = import.meta.env.VITE_API_BASE_URL || "";
  return `${base}${path}`;
}
