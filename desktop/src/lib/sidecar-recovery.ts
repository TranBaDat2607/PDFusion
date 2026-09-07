export const SIDECAR_CRASH_STORAGE_KEY = "pdfusion.sidecar-crash-at";
export const SIDECAR_RESTART_COOLDOWN_MS = 60_000;

// lastAttempt/now are parameters rather than localStorage/Date.now() reads,
// so this stays pure and testable with zero mocking.
export function shouldAutoRestart(lastAttempt: number | null, now: number): boolean {
  return lastAttempt === null || now - lastAttempt > SIDECAR_RESTART_COOLDOWN_MS;
}
