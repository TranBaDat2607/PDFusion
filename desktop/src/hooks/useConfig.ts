import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api-client";

export interface ServiceConfig {
  has_key: boolean;
  model: string;
}

export interface ConfigResponse {
  openai: ServiceConfig;
  gemini: ServiceConfig;
  anthropic: ServiceConfig;
  translation: {
    default_source_lang: string;
    default_target_lang: string;
    preferred_service: "openai" | "gemini" | "anthropic";
    max_pages: number;
    max_file_size_mb: number;
  };
  rag: { enabled: boolean };
  deep_search: Record<string, unknown>;
  gui: Record<string, unknown>;
  debug_mode: boolean;
}

export interface LanguageOption {
  code: string;
  label: string;
}

export interface ServiceOption {
  code: "openai" | "gemini" | "anthropic";
  label: string;
  models: string[];
}

export interface OptionsResponse {
  languages: LanguageOption[];
  services: ServiceOption[];
}

export interface ConfigUpdate {
  openai?: { api_key?: string | null; model?: string };
  gemini?: { api_key?: string | null; model?: string };
  anthropic?: { api_key?: string | null; model?: string };
  preferred_service?: "openai" | "gemini" | "anthropic";
  default_source_lang?: string;
  default_target_lang?: string;
  rag_enabled?: boolean;
}

export interface ValidateResponse {
  valid: boolean;
  message: string;
}

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: () => api.get<ConfigResponse>("/config"),
  });
}

export function useOptions() {
  return useQuery({
    queryKey: ["config", "options"],
    queryFn: () => api.get<OptionsResponse>("/config/options"),
    staleTime: Infinity,
  });
}

export function useUpdateConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (update: ConfigUpdate) =>
      api.put<ConfigResponse>("/config", update),
    onSuccess: (data) => qc.setQueryData(["config"], data),
  });
}

export function useValidateCredentials() {
  return useMutation({
    mutationFn: (input: {
      service: "openai" | "gemini" | "anthropic";
      api_key: string;
      model?: string;
    }) => api.post<ValidateResponse>("/config/validate", input),
  });
}
