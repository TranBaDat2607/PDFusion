import { useEffect, useMemo, useState } from "react";

import { createLatestThrottle } from "@/lib/throttle-latest";

/**
 * `text` as it should reach the screen: at most one change per `intervalMs`
 * while it streams in (#113). An empty string is never held back, so the
 * previous answer's draft cannot show for a frame when the next one starts.
 */
export function useThrottledText(text: string, intervalMs: number): string {
  const [shown, setShown] = useState(text);
  const throttle = useMemo(
    () => createLatestThrottle<string>(intervalMs, setShown),
    [intervalMs],
  );

  useEffect(() => {
    throttle.push(text);
  }, [throttle, text]);
  useEffect(() => () => throttle.cancel(), [throttle]);

  return text === "" ? "" : shown;
}
