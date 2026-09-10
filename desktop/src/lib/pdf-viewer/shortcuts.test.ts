import { describe, expect, it } from "vitest";

import { matchShortcut, type ShortcutKeyEvent } from "./shortcuts";

function key(
  keyName: string,
  modifiers: Partial<ShortcutKeyEvent> = {},
): ShortcutKeyEvent {
  return {
    key: keyName,
    code: "",
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    metaKey: false,
    ...modifiers,
  };
}

const ctrl = { ctrlKey: true };

describe("matchShortcut", () => {
  it.each([
    [key("o", ctrl), "open"],
    [key("O", ctrl), "open"],
    [key("f", ctrl), "find"],
    [key("=", ctrl), "zoom-in"],
    [key("+", { ...ctrl, shiftKey: true }), "zoom-in"],
    [key("+", { ...ctrl, code: "NumpadAdd" }), "zoom-in"],
    [key("-", ctrl), "zoom-out"],
    [key("-", { ...ctrl, code: "NumpadSubtract" }), "zoom-out"],
    [key("0", ctrl), "zoom-reset"],
    [key("Insert", { ...ctrl, code: "Numpad0" }), "zoom-reset"],
    [key("F3"), "find-next"],
    [key("F3", { shiftKey: true }), "find-previous"],
  ] as const)("maps %o to %s", (event, expected) => {
    expect(matchShortcut(event)).toBe(expected);
  });

  it.each([
    ["an unmodified letter", key("f")],
    ["Ctrl+Shift+O", key("O", { ...ctrl, shiftKey: true })],
    ["Ctrl+Shift+F", key("F", { ...ctrl, shiftKey: true })],
    ["Ctrl+Alt+F, which is AltGr on many layouts", key("f", { ...ctrl, altKey: true })],
    ["a Windows-key combination", key("f", { ...ctrl, metaKey: true })],
    ["Ctrl+F3", key("F3", ctrl)],
    ["a key pressed mid-composition", key("f", { ...ctrl, isComposing: true })],
    ["Ctrl+ an unrelated key", key("s", ctrl)],
  ])("ignores %s", (_label, event) => {
    expect(matchShortcut(event)).toBeNull();
  });
});
