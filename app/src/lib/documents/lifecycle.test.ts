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

  it("a failed delete keeps the panel open and puts focus back inside it, on the error", () => {
    // The button that was pressed went disabled while the request ran, so without this
    // focus would fall to the page.
    expect(run(["ask", "failed"])).toEqual({ open: true, focus: "confirm" });
    // And it can still be cancelled, which returns focus to Delete as before.
    expect(run(["ask", "failed", "cancel"])).toEqual({ open: false, focus: "trigger" });
  });

  it("an archive that completes closes an open panel and moves focus to the Restore button", () => {
    expect(run(["ask", "archived"])).toEqual({ open: false, focus: "restore" });
    // With no panel open the pressed Archive button is still replaced, so focus still moves.
    expect(run(["archived"])).toEqual({ open: false, focus: "restore" });
  });

  it("a restore that completes moves focus to the Archive button that replaced it", () => {
    expect(run(["restored"])).toEqual({ open: false, focus: "archive" });
    expect(run(["ask", "restored"])).toEqual({ open: false, focus: "archive" });
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
