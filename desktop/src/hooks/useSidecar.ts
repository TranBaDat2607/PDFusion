/**
 * Tracks sidecar boot status. Returns one of: "starting" | "ready" | "error".
 */

import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { setSidecar, type SidecarInfo } from "@/lib/api-client";
import { waitForTauri } from "@/lib/tauri-ready";

type SidecarState =
  | { status: "starting" }
  | { status: "ready"; info: SidecarInfo }
  | { status: "error"; message: string };

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
