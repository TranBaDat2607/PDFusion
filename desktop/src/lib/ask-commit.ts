import { IDLE_ASK, type AskState } from "@/lib/rag-ask";
import { createLatestThrottle } from "@/lib/throttle-latest";

/**
 * Folds a question's events into a state and hands it to React, but not every
 * `answer_delta` of it (#113). A delta arrives every ~30 ms, and each commit
 * renders the whole chat panel — its saved answers included — so a commit per
 * delta was the bulk of the work while an answer streamed in.
 *
 * The state is folded as each event arrives, held here, and only its commit is
 * throttled: a held-back delta is part of the next state whatever else comes,
 * and any other event (the answer, an error, a progress line) commits at once,
 * so the end of a question is never behind a timer.
 */
export function createAskCommitter(
  commit: (state: AskState) => void,
  intervalMs: number,
) {
  let latest: AskState = IDLE_ASK;
  const throttle = createLatestThrottle<AskState>(intervalMs, commit);

  return {
    /** `coalesce` for a delta; anything else commits now. */
    fold(step: (state: AskState) => AskState, coalesce: boolean) {
      latest = step(latest);
      if (coalesce) {
        throttle.push(latest);
      } else {
        throttle.cancel();
        commit(latest);
      }
    },
    /** A new question, or none: what the last one held back is no longer wanted. */
    set(state: AskState) {
      throttle.cancel();
      latest = state;
      commit(state);
    },
    cancel() {
      throttle.cancel();
    },
  };
}
