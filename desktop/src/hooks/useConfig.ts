import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api-client";

export type ServiceCode = "openai" | "gemini" | "anthropic" | "argos";

export interface ServiceConfig {
  has_key: boolean;
  model: string;
}

export interface ConfigResponse {
  openai: ServiceConfig;
  gemini: ServiceConfig;
  anthropic: ServiceConfig;
  argos: ServiceConfig;
  translation: {
    default_source_lang: string;
    default_target_lang: string;
    preferred_service: ServiceCode;
    max_pages: number;
    max_file_size_mb: number;
    cache_translations?: boolean;
    cache_ttl_days?: number;
    cache_max_size_mb?: number;
    cache_translated_pdfs?: boolean;
    pdf_cache_max_size_mb?: number;
  };
  rag: { enabled: boolean };
  // No `deep_search` section: deep search / web research were removed in
  // `35bca2c` and `AppSettings` has had no such field since.
  gui: Record<string, unknown>;
  processing?: {
    max_workers?: number;
    max_parallel_chunks?: number;
    timeout_seconds?: number;
  };
  debug_mode: boolean;
}

export interface LanguageOption {
  code: string;
  label: string;
}

export interface ServiceOption {
  code: ServiceCode;
  label: string;
  models: string[];
  /** `[source, target]` pairs this backend can produce, or `null` when it has
   *  no restriction. Auto-source aliases are already expanded server-side —
   *  see `translators/capabilities.py:supported_pairs_for`. */
  supported_pairs: string[][] | null;
}

export interface OptionsResponse {
  languages: LanguageOption[];
  services: ServiceOption[];
}

export interface ConfigUpdate {
  openai?: { api_key?: string | null; model?: string };
  gemini?: { api_key?: string | null; model?: string };
  anthropic?: { api_key?: string | null; model?: string };
  // Argos has no key/model to update — intentionally absent.
  preferred_service?: ServiceCode;
  default_source_lang?: string;
  default_target_lang?: string;
  rag_enabled?: boolean;
  max_parallel_chunks?: number;
  cache_translations?: boolean;
  cache_translated_pdfs?: boolean;
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
    onSuccess: (data, update) => {
      const previous = qc.getQueryData<ConfigResponse>(["config"]);
      qc.setQueryData(["config"], data);

      const label = (code: string) => {
        const options = qc.getQueryData<OptionsResponse>(["config", "options"]);
        return options?.services.find((s) => s.code === code)?.label ?? code;
      };

      // Server may auto-promote preferred_service from "argos" to an LLM
      // when the user just saved a key. Surface that to the user.
      if (
        previous &&
        previous.translation.preferred_service !==
          data.translation.preferred_service
      ) {
        toast.success(`Switched to ${label(data.translation.preferred_service)}`);
        return;
      }

      // …and the server only promotes on a key that *validates*. When it
      // doesn't, the save still succeeds but nothing visible changes — which
      // reads as "my key was accepted" for a key the provider just rejected.
      // Same priority the server promotes by (`routes/config.py`): one PUT
      // can carry keys for several services, and only the first by this order
      // is probed — so a different order here would name the wrong provider.
      const savedKeyFor = (["openai", "anthropic", "gemini"] as const).find(
        (code) => update[code]?.api_key,
      );
      if (
        savedKeyFor &&
        previous?.translation.preferred_service === "argos" &&
        data.translation.preferred_service === "argos"
      ) {
        toast.warning(`${label(savedKeyFor)} did not accept that key`, {
          description:
            "The key is saved, but translation stays on Argos (offline). Use Validate to check it.",
        });
      }
    },
  });
}

export function useValidateCredentials() {
  return useMutation({
    mutationFn: (input: {
      service: ServiceCode;
      api_key: string;
      model?: string;
    }) => api.post<ValidateResponse>("/config/validate", input),
  });
}
