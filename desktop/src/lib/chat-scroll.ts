/** How far from the bottom still counts as "at the bottom". It only has to
 *  absorb the sub-pixel `scrollTop` drift of high-DPI displays, where an exact
 *  `=== 0` test would lose a reader who is at the bottom: reaching the bottom
 *  clamps to a distance of 0. Anything wider pins the readers who scroll in
 *  small steps — Arrow-Up moves 40 px and a touchpad a few — because the next
 *  streamed delta lands ~30 ms later and still sees them "near" (#109). */
export const FOLLOW_THRESHOLD_PX = 4;

/** Whether a scroll position is within `FOLLOW_THRESHOLD_PX` of the bottom of
 *  its content. A height below the viewport's gives a negative distance and so
 *  always counts. */
export function isNearBottom({
  scrollTop,
  clientHeight,
  scrollHeight,
}: {
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
}): boolean {
  return scrollHeight - scrollTop - clientHeight <= FOLLOW_THRESHOLD_PX;
}
