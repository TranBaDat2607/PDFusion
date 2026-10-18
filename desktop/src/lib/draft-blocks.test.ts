import { describe, expect, it } from "vitest";

import { splitDraftBlocks } from "./draft-blocks";

// A streamed answer is re-split on every piece, so a block counts as finished
// only when no later text can change it: it ends at a blank line followed by a
// non-indented line. Every block keeps its trailing newlines so the pieces
// concatenate back to the input.
describe("splitDraftBlocks", () => {
  it("returns no blocks and an empty tail for an empty string", () => {
    expect(splitDraftBlocks("")).toEqual({ blocks: [], tail: "" });
  });

  it("keeps a lone paragraph as the tail", () => {
    expect(splitDraftBlocks("just one paragraph\nover two lines")).toEqual({
      blocks: [],
      tail: "just one paragraph\nover two lines",
    });
  });

  it("finishes a paragraph once a non-indented line follows its blank line", () => {
    expect(splitDraftBlocks("a\n\nb")).toEqual({ blocks: ["a\n\n"], tail: "b" });
  });

  // The next line could still turn out to be indented (a list item's
  // continuation), so the blank line alone does not finish anything.
  it("keeps a paragraph and its trailing blank line as the tail until a line follows", () => {
    expect(splitDraftBlocks("a\n\n")).toEqual({ blocks: [], tail: "a\n\n" });
  });

  it("splits three paragraphs into two blocks and a tail", () => {
    expect(splitDraftBlocks("a\n\nb\n\nc")).toEqual({
      blocks: ["a\n\n", "b\n\n"],
      tail: "c",
    });
  });

  it("does not split at a blank line inside a closed ``` fence", () => {
    const fence = "```js\nx\n\ny\n```\n\n";
    expect(splitDraftBlocks(`para\n\n${fence}after`)).toEqual({
      blocks: ["para\n\n", fence],
      tail: "after",
    });
  });

  it("does not split at a blank line inside a closed $$ block", () => {
    const math = "$$\nx\n\ny\n$$\n\n";
    expect(splitDraftBlocks(`para\n\n${math}after`)).toEqual({
      blocks: ["para\n\n", math],
      tail: "after",
    });
  });

  it("makes everything from an unclosed ``` fence onward the tail", () => {
    expect(splitDraftBlocks("intro\n\n```py\nx\n\ny")).toEqual({
      blocks: ["intro\n\n"],
      tail: "```py\nx\n\ny",
    });
  });

  it("makes everything from an unclosed $$ block onward the tail", () => {
    expect(splitDraftBlocks("intro\n\n$$\nx\n\ny")).toEqual({
      blocks: ["intro\n\n"],
      tail: "$$\nx\n\ny",
    });
  });

  it("treats ~~~ like ``` and lets a ``` line inside it pass", () => {
    const fence = "~~~\na\n```\n\nb\n~~~\n\n";
    expect(splitDraftBlocks(`${fence}after`)).toEqual({
      blocks: [fence],
      tail: "after",
    });
  });

  it("does not let a ``` line close an open ~~~ fence", () => {
    // Were the ``` taken as a closing fence, the blank line after it would
    // finish a block and "b" would start the tail.
    expect(splitDraftBlocks("~~~\na\n```\n\nb")).toEqual({
      blocks: [],
      tail: "~~~\na\n```\n\nb",
    });
  });

  it("does not split at a blank line before a space-indented line", () => {
    expect(splitDraftBlocks("- a\n\n  b")).toEqual({ blocks: [], tail: "- a\n\n  b" });
  });

  it("does not split at a blank line before a tab-indented line", () => {
    expect(splitDraftBlocks("- a\n\n\tb")).toEqual({ blocks: [], tail: "- a\n\n\tb" });
  });

  it("finishes a list with its indented continuation at the next non-indented line", () => {
    expect(splitDraftBlocks("- a\n\n  b\n\nc")).toEqual({
      blocks: ["- a\n\n  b\n\n"],
      tail: "c",
    });
  });

  it("keeps several consecutive blank lines with the preceding block", () => {
    expect(splitDraftBlocks("a\n\n\n\nb")).toEqual({ blocks: ["a\n\n\n\n"], tail: "b" });
  });

  describe("over every prefix of a realistic answer", () => {
    const ANSWER = [
      "The invoice total is stated on the first page.\n",
      "\n",
      "## Line items\n",
      "\n",
      "- Widgets: 3 units\n",
      "\n",
      "  Delivered in two batches.\n",
      "- Gadgets: 2 units\n",
      "\n",
      "```python\n",
      "total = 3 * 5\n",
      "\n",
      "print(total)\n",
      "```\n",
      "\n",
      "The tax follows:\n",
      "\n",
      "$$\n",
      "t = r \\cdot s\n",
      "\n",
      "u = t + s\n",
      "$$\n",
      "\n",
      "That is all.",
    ].join("");

    it("rejoins to the text it was given", () => {
      for (let n = 0; n <= ANSWER.length; n++) {
        const prefix = ANSWER.slice(0, n);
        const { blocks, tail } = splitDraftBlocks(prefix);
        expect(blocks.join("") + tail, `prefix of ${n} characters`).toBe(prefix);
      }
    });

    it("never changes an earlier block as text grows", () => {
      let previous = splitDraftBlocks("").blocks;
      for (let n = 1; n <= ANSWER.length; n++) {
        const { blocks } = splitDraftBlocks(ANSWER.slice(0, n));
        expect(blocks.slice(0, previous.length), `prefix of ${n} characters`).toEqual(previous);
        expect(blocks.length, `prefix of ${n} characters`).toBeGreaterThanOrEqual(previous.length);
        previous = blocks;
      }
    });
  });
});
