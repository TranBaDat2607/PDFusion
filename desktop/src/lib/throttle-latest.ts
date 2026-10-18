/**
 * Emits the first value at once, then at most one value per `intervalMs` —
 * the latest pushed since. A streamed answer pushes a delta every ~30 ms, and
 * only the newest text is worth drawing (#113).
 */
export function createLatestThrottle<T>(
  intervalMs: number,
  emit: (value: T) => void,
) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending: { value: T } | null = null;

  // The trailing emit opens a window of its own: without it a steady stream
  // would alternate between an immediate emit and a delayed one, and the rate
  // would not be bounded.
  const open = () => {
    timer = setTimeout(() => {
      timer = null;
      if (pending) {
        const { value } = pending;
        pending = null;
        emit(value);
        open();
      }
    }, intervalMs);
  };

  const cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    pending = null;
  };

  return {
    push(value: T) {
      if (timer) {
        pending = { value };
        return;
      }
      emit(value);
      open();
    },
    cancel,
    flush() {
      const held = pending;
      cancel();
      if (held) emit(held.value);
    },
  };
}
