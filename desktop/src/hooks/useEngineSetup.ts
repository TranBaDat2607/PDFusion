import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api-client";
import type { EngineStatus } from "@/lib/engine-setup";
import { markSkipped } from "@/lib/engine-setup";

/**
 * How often the status endpoint is asked while an install runs. It is stat
 * calls over a 183-entry manifest, so this is cheap; the download it is
 * watching takes minutes.
 */
const POLL_INTERVAL_MS = 500;

export interface EngineSetupState {
  /** null until the first `/setup/status` lands, and after a probe failure. */
  engine: EngineStatus | null;
  /** The probe itself hasn't answered yet — distinct from "nothing installed". */
  probing: boolean;
  /** The status probe failed. Install failures live on `engine.install.error`. */
  probeError?: string;
}

function errorMessage(e: unknown): string {
  // ApiError.detail is the sentence the sidecar wrote for the user; its
  // `message` prefixes the status code onto it.
  return e instanceof ApiError ? e.detail : (e as Error).message;
}

/**
 * Installing the offline translation engine, and knowing whether it needs it.
 *
 * Polling rather than an SSE stream, because the install is a process-wide
 * singleton that outlives any one client: a reload mid-install re-attaches to
 * it here on the next poll, where a dropped stream would have looked like a
 * failure and invited a second download. See `api/routes/setup.py`.
 */
export function useEngineSetup() {
  const [state, setState] = useState<EngineSetupState>({ engine: null, probing: true });

  const refresh = useCallback(async () => {
    try {
      const engine = await api.get<EngineStatus>("/setup/status");
      setState({ engine, probing: false });
    } catch (e) {
      // Leave `engine` null: `shouldShowSetup` reads that as "don't gate the
      // app on a probe that didn't answer".
      setState({ engine: null, probing: false, probeError: errorMessage(e) });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const running = state.engine?.install.running ?? false;

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [running, refresh]);

  const install = useCallback(async () => {
    try {
      // The POST answers with the same snapshot /setup/status does, so this
      // both starts the install and turns the polling effect on in one round
      // trip — no interim state to invent.
      const engine = await api.post<EngineStatus>("/setup/engine");
      setState({ engine, probing: false });
    } catch (e) {
      setState((s) => ({ ...s, probing: false, probeError: errorMessage(e) }));
    }
  }, []);

  const skip = useCallback(() => {
    markSkipped();
  }, []);

  return { state, refresh, install, skip };
}
