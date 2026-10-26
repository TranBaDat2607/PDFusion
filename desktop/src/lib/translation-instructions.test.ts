import { describe, expect, it } from "vitest";

import {
  MAX_INSTRUCTIONS_CHARS,
  instructionsApply,
  instructionsDraft,
} from "./translation-instructions";

describe("instructionsDraft", () => {
  it("is unchanged when the draft matches what is saved", () => {
    const draft = instructionsDraft("Keep index in English", "Keep index in English");
    expect(draft.dirty).toBe(false);
  });

  it("sends the draft trimmed, as the sidecar would store it", () => {
    const draft = instructionsDraft("  Keep index in English \n", null);
    expect(draft.value).toBe("Keep index in English");
    expect(draft.dirty).toBe(true);
  });

  // The sidecar trims too, so only surrounding whitespace never makes a
  // difference worth a Save.
  it("ignores whitespace around a saved value", () => {
    expect(instructionsDraft("Formal tone  ", "Formal tone").dirty).toBe(false);
  });

  it("sends null for an empty or blank draft, which clears the saved one", () => {
    expect(instructionsDraft("   ", "Formal tone")).toMatchObject({ value: null, dirty: true });
    expect(instructionsDraft("", null)).toMatchObject({ value: null, dirty: false });
  });

  it("treats a config without the field as nothing saved", () => {
    expect(instructionsDraft("", undefined).dirty).toBe(false);
  });

  it("is too long one character past the limit, counted after trimming", () => {
    const atLimit = "a".repeat(MAX_INSTRUCTIONS_CHARS);
    expect(instructionsDraft(`  ${atLimit}  `, null).tooLong).toBe(false);
    expect(instructionsDraft(`${atLimit}a`, null).tooLong).toBe(true);
  });

  it("matches the sidecar's limit", () => {
    expect(MAX_INSTRUCTIONS_CHARS).toBe(2000);
  });
});

describe("instructionsApply", () => {
  const llm = { id: "gemini", is_llm: true, requires_key: true, has_key: true };
  const argos = { id: "argos", is_llm: false, requires_key: false, has_key: false };
  const config = (provider: string, instructions: string | null) => ({
    translation: { model: { provider, model: "m" }, instructions },
  });

  it("applies when instructions are saved and an LLM translates", () => {
    expect(instructionsApply(config("gemini", "Keep index"), [llm, argos])).toBe(true);
  });

  it("does not apply with nothing saved", () => {
    expect(instructionsApply(config("gemini", null), [llm, argos])).toBe(false);
  });

  it("does not apply when Argos translates", () => {
    expect(instructionsApply(config("argos", "Keep index"), [llm, argos])).toBe(false);
  });

  // The sidecar runs Argos in place of an LLM with no key, so the toolbar
  // must not claim instructions that will never be sent.
  it("does not apply to an LLM with no key, which runs as Argos", () => {
    const keyless = { ...llm, has_key: false };
    expect(instructionsApply(config("gemini", "Keep index"), [keyless, argos])).toBe(false);
  });
});
