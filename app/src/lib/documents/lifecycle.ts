// The delete confirm panel's behaviour as a small state machine, apart from the view so
// it is tested. The view dispatches events and moves focus where `focus` says.
//
// The rules (WAI-ARIA alertdialog, and why each is here):
//   - Opening the panel moves focus INTO it (to Cancel, the safe action), so a keyboard
//     or screen-reader user is not left on a button that has just disappeared.
//   - Cancelling puts focus back on the Delete button that opened it.
//   - Escape cancels, so the panel is operable without a pointer.
//   - A finished delete leaves the page; there is nowhere to put focus.
//   - A FAILED delete keeps the panel open (the error is shown inside it) and puts focus
//     back inside it, because the button that was pressed went disabled while it ran and
//     focus would otherwise fall to the page.
//   - An archive or a restore that completes swaps the Archive and Restore buttons, so the
//     one that was pressed is gone: the panel (if open) closes and focus goes to the button
//     that replaced it.

export type ConfirmFocus = "confirm" | "trigger" | "restore" | "archive" | null;
export interface ConfirmState {
  open: boolean;
  /** Where focus should go as a result of the last event. */
  focus: ConfirmFocus;
}
export type ConfirmEvent = "ask" | "cancel" | "done" | "failed" | "archived" | "restored";

export const CONFIRM_CLOSED: ConfirmState = { open: false, focus: null };

export function confirmReducer(_state: ConfirmState, event: ConfirmEvent): ConfirmState {
  switch (event) {
    case "ask":
      return { open: true, focus: "confirm" };
    case "cancel":
      return { open: false, focus: "trigger" };
    case "done":
      return { open: false, focus: null };
    case "failed":
      return { open: true, focus: "confirm" };
    case "archived":
      return { open: false, focus: "restore" };
    case "restored":
      return { open: false, focus: "archive" };
  }
}

/** The event a key press means inside the open panel, or null for any other key. */
export function confirmKey(key: string): ConfirmEvent | null {
  return key === "Escape" ? "cancel" : null;
}
