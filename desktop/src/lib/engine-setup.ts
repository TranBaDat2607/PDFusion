/**
 * Whether the first-run setup screen should be shown, and the one bit of
 * state that survives a reload.
 *
 * Pure, and takes what it needs as arguments rather than reading
 * localStorage/Date.now() itself, so it is testable without mocks — the same
 * shape as `sidecar-recovery.ts`.
 */

export interface EngineAssetGroup {
  id: string;
  label: string;
  ready: boolean;
  present: number;
  total: number;
  detail: string;
}

export interface EngineInstallState {
  running: boolean;
  stage: string | null;
  /** The last install's failure, kept after it ends. */
  error: string | null;
}

export interface EngineStatus {
  ready: boolean;
  groups: EngineAssetGroup[];
  /** The installer shipped the assets, so setup is a local unzip. */
  bundled: boolean;
  install: EngineInstallState;
}

export const ENGINE_SETUP_SKIPPED_KEY = "pdfusion.engine-setup-skipped";

/**
 * The user chose "Not now" at least once.
 *
 * Kept in localStorage rather than in a module or React variable because it has
 * to outlive a reload; the app is otherwise happy to re-show a screen the user
 * has already dismissed.
 */
export function readSkipped(): boolean {
  try {
    return window.localStorage.getItem(ENGINE_SETUP_SKIPPED_KEY) === "1";
  } catch {
    // Private mode, or storage disabled. Falling back to "not skipped" shows
    // the screen again, which is the recoverable direction: the alternative
    // hides setup from someone who has never seen it.
    return false;
  }
}

export function markSkipped(): void {
  try {
    window.localStorage.setItem(ENGINE_SETUP_SKIPPED_KEY, "1");
  } catch {
    // Nothing to do — the screen reappears next launch, which is only an
    // annoyance, and it still has a Not now.
  }
}

export function clearSkipped(): void {
  try {
    window.localStorage.removeItem(ENGINE_SETUP_SKIPPED_KEY);
  } catch {
    // See markSkipped.
  }
}

/**
 * `forced` is the mid-session route in: a Translate that came back 409 because
 * the engine isn't installed. That has to win over a previous skip — the user
 * has now asked for the thing the assets are needed for.
 *
 * `status === null` means the status probe itself failed. Don't show setup for
 * that: the sidecar is reachable (the boot screen already gated on it), so a
 * failed probe is a bug on our side, and blocking the app behind a screen whose
 * own data is missing helps nobody.
 */
export function shouldShowSetup(
  status: EngineStatus | null,
  skipped: boolean,
  forced: boolean,
): boolean {
  if (forced) return true;
  if (status === null) return false;
  return !status.ready && !skipped;
}
