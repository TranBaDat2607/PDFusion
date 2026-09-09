import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-types";

// Generated from `api/schemas.py` (see `desktop/src/lib/openapi.json` /
// `api-types.d.ts`) instead of hand-copied. Three of these had already
// drifted from the backend in production before this existed: language
// fields silently never sent, `pdf_references` vs. `pdf_sources` (#13), and a
// dead `deep_search`/web-research pair of fields (#14) — see issue #27.
export type ServiceCode = components["schemas"]["TranslationService"];
export type ServiceConfig = components["schemas"]["APIKeyMaskedSettings"];
export type ConfigResponse = components["schemas"]["ConfigResponse"];
export type LanguageOption = components["schemas"]["LanguageOption"];
export type ServiceOption = components["schemas"]["ServiceOption"];
export type OptionsResponse = components["schemas"]["OptionsResponse"];
export type ConfigUpdate = components["schemas"]["ConfigUpdateRequest"];
export type ValidateResponse = components["schemas"]["ValidateResponse"];

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
