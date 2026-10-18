/** How far from the bottom still counts as "at the bottom". Under one wheel
 *  tick (~100 px in Chromium), so a single tick up stops the panel following
 *  a streaming answer; over the sub-pixel `scrollTop` drift of high-DPI
 *  displays, where an exact `=== 0` test would lose the reader (#109). */
export const FOLLOW_THRESHOLD_PX = 48;

/** Whether a scroll position is within `threshold` of the bottom of its
 *  content. Content shorter than the viewport has a negative distance and so
 *  always counts. */
export function isNearBottom(
  {
    scrollTop,
    clientHeight,
    scrollHeight,
  }: { scrollTop: number; clientHeight: number; scrollHeight: number },
  threshold: number = FOLLOW_THRESHOLD_PX,
): boolean {
  return scrollHeight - scrollTop - clientHeight <= threshold;
}
