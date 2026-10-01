import { describe, expect, it } from "vitest";
import { CONFIRM_CLOSED, confirmKey, confirmReducer, type ConfirmEvent, type ConfirmState } from "./lifecycle";

const run = (events: ConfirmEvent[], from: ConfirmState = CONFIRM_CLOSED) =>
  events.reduce(confirmReducer, from);

describe("the delete confirm panel (d)", () => {
  it("starts closed with no focus to move", () => {
    expect(CONFIRM_CLOSED).toEqual({ open: false, focus: null });
  });

  it("opening it moves focus INTO the panel", () => {
    expect(run(["ask"])).toEqual({ open: true, focus: "confirm" });
  });

  it("cancelling closes it and restores focus to the Delete button that opened it", () => {
    expect(run(["ask", "cancel"])).toEqual({ open: false, focus: "trigger" });
    // Opened again, it takes focus again: the focus target is per event, not remembered.
    expect(run(["ask", "cancel", "ask"])).toEqual({ open: true, focus: "confirm" });
  });

  it("a finished delete closes it and leaves no focus to restore (the page is gone)", () => {
    expect(run(["ask", "done"])).toEqual({ open: false, focus: null });
  });

  it("is operable from the keyboard: Escape cancels, no other key does anything", () => {
    expect(confirmKey("Escape")).toBe("cancel");
    for (const key of ["Enter", " ", "Tab", "ArrowDown", "Delete", "Backspace", "x"]) {
      expect(confirmKey(key), key).toBeNull();
    }
    // Escape inside an open panel is the same as pressing Cancel.
    const event = confirmKey("Escape");
    expect(event && run(["ask", event])).toEqual(run(["ask", "cancel"]));
  });
});
