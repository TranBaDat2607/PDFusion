/**
 * Tracks sidecar boot status. Returns one of:
 * "starting" | "ready" | "error" | "crashed".
 */

import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { resetSidecar, setSidecar, type SidecarInfo } from "@/lib/api-client";
import { waitForTauri } from "@/lib/tauri-ready";
import { shouldAutoRestart, SIDECAR_CRASH_STORAGE_KEY } from "@/lib/sidecar-recovery";

type SidecarState =
  | { status: "starting" }
  | { status: "ready"; info: SidecarInfo }
  | { status: "error"; message: string }
  | { status: "crashed"; code: number | null };

export function useSidecar(): SidecarState {
  const [state, setState] = useState<SidecarState>({ status: "starting" });

  useEffect(() => {
    let cancelled = false;
    const subs: Array<() => void> = [];

    (async () => {
      try {
        await waitForTauri();
      } catch (e) {
        if (!cancelled) {
          setState({ status: "error", message: (e as Error).message });
        }
        return;
      }

      const ready = (info: SidecarInfo) => {
        if (cancelled) return;
        setSidecar(info);
        localStorage.removeItem(SIDECAR_CRASH_STORAGE_KEY);
        setState({ status: "ready", info });
      };

      try {
        subs.push(
          await listen<SidecarInfo>("sidecar://ready", (e) => ready(e.payload)),
        );
        subs.push(
          await listen<string>("sidecar://error", (e) => {
            if (!cancelled) setState({ status: "error", message: e.payload });
          }),
        );
        subs.push(
          await listen<{ code: number | null }>("sidecar://exited", (e) => {
            if (cancelled) return;
            resetSidecar();
            const raw = localStorage.getItem(SIDECAR_CRASH_STORAGE_KEY);
            const lastAttempt = raw === null ? null : Number(raw);
            const now = Date.now();
            if (shouldAutoRestart(lastAttempt, now)) {
              localStorage.setItem(SIDECAR_CRASH_STORAGE_KEY, String(now));
              setState({ status: "starting" });
              void invoke("restart_app");
            } else {
              setState({ status: "crashed", code: e.payload.code });
            }
          }),
        );

        const status = await invoke<{
          ready: boolean;
          info: SidecarInfo | null;
          error: string | null;
        }>("sidecar_info");
        if (status.ready && status.info) ready(status.info);
      } catch (e) {
        if (!cancelled) {
          setState({ status: "error", message: (e as Error).message });
        }
      }
    })();

    return () => {
      cancelled = true;
      subs.forEach((u) => u());
    };
  }, []);

  return state;
}
