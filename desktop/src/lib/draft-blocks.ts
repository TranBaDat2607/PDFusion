/**
 * Splits a streamed Markdown answer into blocks that no later text can change,
 * and the unfinished tail (#113). The draft is shown while the model writes
 * it, once per `answer_delta`; parsing all of it each time made the work grow
 * with the square of the answer, so only the tail is parsed again and the
 * blocks are rendered once and memoized.
 *
 * A block ends at a blank line followed by a non-indented line. The line after
 * the blank decides, so a blank line that is the last thing received finishes
 * nothing yet: it may turn out to be a list item's continuation, which parsed
 * alone would become a code block. Blank lines inside a fence or a `$$` block
 * never end one. Each block keeps its trailing blank lines, so
 * `blocks.join("") + tail === text`.
 */
export function splitDraftBlocks(text: string): {
  blocks: string[];
  tail: string;
} {
  const blocks: string[] = [];
  let blockStart = 0;
  let offset = 0;
  let blankRun = false;
  // The marker that closes what is open; "" outside a fence or math block.
  let closer = "";

  for (const line of text.match(/[^\n]*\n|[^\n]+$/g) ?? []) {
    const body = line.replace(/\r?\n$/, "");
    const trimmed = body.trim();

    // A whitespace-only line with no newline yet may still grow into an
    // indented one, so only a line that is over counts as blank.
    if (trimmed === "" && line.endsWith("\n")) {
      blankRun = true;
    } else {
      if (blankRun && closer === "" && !/^\s/.test(body)) {
        blocks.push(text.slice(blockStart, offset));
        blockStart = offset;
      }
      blankRun = false;
      closer = nextCloser(closer, trimmed);
    }
    offset += line.length;
  }

  return { blocks, tail: text.slice(blockStart) };
}

/** What the line, if not blank, does to the open fence or math block. */
function nextCloser(closer: string, trimmed: string): string {
  if (closer === "$$") return trimmed === "$$" ? "" : closer;
  if (closer !== "") {
    // CommonMark: a closing fence is the same character, at least as long.
    const closes =
      trimmed.length >= closer.length && trimmed === closer[0].repeat(trimmed.length);
    return closes ? "" : closer;
  }
  if (trimmed === "$$") return "$$";
  const fence = /^(`{3,}|~{3,})/.exec(trimmed);
  return fence ? fence[1] : "";
}
