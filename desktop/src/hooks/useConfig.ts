import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-types";
import { PROVIDERS_KEY, type ProviderInfo } from "@/hooks/useProviders";

// Generated from `api/schemas.py` (see `desktop/src/lib/openapi.json` /
// `api-types.d.ts`) instead of hand-copied. Three of these had already
// drifted from the backend in production before this existed: language
// fields silently never sent, `pdf_references` vs. `pdf_sources` (#13), and a
// dead `deep_search`/web-research pair of fields (#14) — see issue #27.
export type ServiceCode = components["schemas"]["TranslationService"];
export type ConfigResponse = components["schemas"]["ConfigResponse"];
export type LanguageOption = components["schemas"]["LanguageOption"];
export type ServiceOption = components["schemas"]["ServiceOption"];
export type OptionsResponse = components["schemas"]["OptionsResponse"];
export type ConfigUpdate = components["schemas"]["ConfigUpdateRequest"];
export type ValidateRequest = components["schemas"]["ValidateRequest"];
export type ValidateResponse = components["schemas"]["ValidateResponse"];

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: () => api.get<ConfigResponse>("/config"),
  });
}

/** Whether chat is turned on in Settings → Chat. `false` until the config has
 *  loaded, so the Chat button doesn't flash in and out on startup. */
export function useChatEnabled(): boolean {
  const { data } = useConfig();
  return data?.rag.chat_enabled ?? false;
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
    onSuccess: (data) => {
      const previous = qc.getQueryData<ConfigResponse>(["config"]);
      qc.setQueryData(["config"], data);
      // A provider's `model` is the translation model while it translates
      // (`AppSettings.model_for`), so a new choice changes the providers too.
      void qc.invalidateQueries({ queryKey: PROVIDERS_KEY, exact: true });

      const provider = data.translation.model.provider;
      if (previous && previous.translation.model.provider !== provider) {
        const providers = qc.getQueryData<ProviderInfo[]>(PROVIDERS_KEY);
        const label = providers?.find((p) => p.id === provider)?.label ?? provider;
        toast.success(`Switched to ${label}`);
      }
    },
  });
}

/** Check credentials with the provider, by listing the key's models (never a
 *  completion) and looking the model up there. Whatever the request leaves out
 *  (the key, the model, the endpoint) the sidecar takes from the saved
 *  settings, and it sends a saved key only to the saved endpoint. */
export function validateCredentials(input: ValidateRequest) {
  return api.post<ValidateResponse>("/config/validate", input);
}

export function useValidateCredentials() {
  return useMutation({ mutationFn: validateCredentials });
}
