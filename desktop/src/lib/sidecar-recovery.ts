export const SIDECAR_CRASH_STORAGE_KEY = "pdfusion.sidecar-crash-at";
export const SIDECAR_RESTART_TOAST_KEY = "pdfusion.sidecar-restart-toast";
export const SIDECAR_RESTART_COOLDOWN_MS = 60_000;

// lastAttempt/now are parameters rather than localStorage/Date.now() reads,
// so this stays pure and testable with zero mocking. Never cleared on a
// successful boot — only time expires it — so a sidecar that crashes on
// every boot still trips the brake instead of relaunching forever.
export function shouldAutoRestart(lastAttempt: number | null, now: number): boolean {
  return lastAttempt === null || now - lastAttempt > SIDECAR_RESTART_COOLDOWN_MS;
}

// Corrupted or hand-edited storage must fail toward "no prior crash," never
// toward a NaN that permanently reads as inside the cooldown window.
export function parseCrashTimestamp(raw: string | null): number | null {
  if (raw === null) return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}
