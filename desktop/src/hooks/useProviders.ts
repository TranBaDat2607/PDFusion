import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-types";

export type ProviderInfo = components["schemas"]["ProviderInfo"];
export type ProvidersResponse = components["schemas"]["ProvidersResponse"];
export type ProviderUpdate = components["schemas"]["ProviderUpdateRequest"];
export type ModelCatalog = components["schemas"]["ModelCatalogResponse"];
export type VerifyRequest = components["schemas"]["VerifyRequest"];
export type VerifyResponse = components["schemas"]["VerifyResponse"];

export const PROVIDERS_KEY = ["providers"] as const;

/** A provider's model list, keyed on its endpoint as well as its id: the
 *  list belongs to the server that answered it. */
export function providerModelsKey(id: string, baseUrl: string | null | undefined) {
  return ["providers", id, "models", baseUrl ?? null] as const;
}

const path = (id: string) => `/providers/${encodeURIComponent(id)}`;

/** Every provider in registry order, with where its key stands. Reading it
 *  never lists models, so it is cheap to refetch. */
export function useProviders() {
  return useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: async () => (await api.get<ProvidersResponse>("/providers")).providers,
  });
}

/**
 * The models a provider's saved key can use at its saved endpoint. Served
 * from the sidecar's catalog while fresh, so an open is usually free; a failed
 * listing still arrives as a 200 carrying `error`, which is kept stale so the
 * next open asks again (the cure — starting a local server, fixing a key —
 * happens outside this key).
 */
export function useProviderModels(provider: ProviderInfo | undefined, enabled: boolean) {
  return useQuery({
    queryKey: providerModelsKey(provider?.id ?? "", provider?.base_url),
    queryFn: () => api.get<ModelCatalog>(`${path(provider!.id)}/models`),
    enabled: enabled && !!provider,
    staleTime: ({ state }: { state: { data?: ModelCatalog } }) =>
      state.data?.error ? 0 : 60_000,
  });
}

/** List the models again, past the catalog, and put the answer in place. */
export async function refreshProviderModels(
  qc: ReturnType<typeof useQueryClient>,
  provider: ProviderInfo,
): Promise<ModelCatalog> {
  const catalog = await api.get<ModelCatalog>(`${path(provider.id)}/models?refresh=true`);
  qc.setQueryData(providerModelsKey(provider.id, provider.base_url), catalog);
  void qc.invalidateQueries({ queryKey: PROVIDERS_KEY, exact: true });
  return catalog;
}

/** Check a key by listing its models, saving nothing. Never a completion (#84). */
export function verifyProvider(id: string, body: VerifyRequest) {
  return api.post<VerifyResponse>(`${path(id)}/verify`, body);
}

/** Save one provider's key, endpoint and models. The sidecar drops its own
 *  catalog rows on a key or endpoint change; the lists cached here go too. */
export function useSaveProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, update }: { id: string; update: ProviderUpdate }) =>
      api.put<ProviderInfo>(path(id), update),
    onSuccess: (saved) => afterProviderChange(qc, saved),
  });
}

export function useDeleteProviderKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<ProviderInfo>(`${path(id)}/key`),
    onSuccess: (saved) => afterProviderChange(qc, saved),
  });
}

function afterProviderChange(qc: ReturnType<typeof useQueryClient>, saved: ProviderInfo) {
  qc.setQueryData<ProviderInfo[]>(PROVIDERS_KEY, (current) =>
    current?.map((p) => (p.id === saved.id ? saved : p)),
  );
  void qc.invalidateQueries({ queryKey: ["providers", saved.id, "models"] });
  // The model a provider runs (`GET /config`'s blocks) follows its
  // `enabled_models`.
  void qc.invalidateQueries({ queryKey: ["config"], exact: true });
}
