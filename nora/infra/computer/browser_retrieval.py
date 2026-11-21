"""The page's text and numbered actions for one decision, and the dispatch of the chosen one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from nora.infra.computer.browser import BrowserError, BrowserPage

# Every decision offers at most this many IDs: elements, two scrolls, finish and the person ID.
MAX_ACTIONS = 64
_OFFERED = MAX_ACTIONS - 4
_MAX_LABEL = 80

# ponytail: a click runs the page's own script, as loading the page does; the boundary
# against page scripts is the anonymous tab and scoped egress, not this check.

# The same isolated DOM world serves observation and dispatch: the live control and its
# options are compared before acting, in the same evaluation.
_CONTROL = """function(expected, action, option) {
    if (!this.isConnected || !this.getClientRects().length ||
        getComputedStyle(this).visibility !== 'visible' ||
        this.matches(':disabled,[aria-disabled="true"],[readonly]')) return null;
    let label = (this.getAttribute('aria-label') || this.innerText ||
                 this.value || '').slice(0, 80);
    let kind, target = '', options = [];
    if (this instanceof HTMLSelectElement && !this.multiple) {
        kind = 'select';
        options = Array.from(this.options).slice(0, 64).flatMap((item, index) =>
            !item.disabled && !item.hidden && !item.closest('optgroup:disabled')
            ? [[index, item.text.slice(0, 80), item.value.slice(0, 256), item.selected]]
            : []);
    } else if (this instanceof HTMLAnchorElement) {
        if (!this.hasAttribute('href') || this.hasAttribute('download') ||
            !['http:', 'https:'].includes(this.protocol) ||
            this.username || this.password || this.href.length > 2048) return null;
        kind = 'link';
        target = this.href;
    } else if (this instanceof HTMLTextAreaElement ||
               (this instanceof HTMLInputElement &&
                ['text', 'search', 'email', 'url', 'tel', 'number'].includes(this.type))) {
        kind = 'type';
        label = (this.getAttribute('aria-label') || this.placeholder || this.name ||
                 this.id || '').slice(0, 80);
        // Enter submits a lone field or a search box; in a longer form it would submit early.
        const fields = this.form ? this.form.querySelectorAll(
            'input:not([type=hidden],[type=checkbox],[type=radio],[type=submit],' +
            '[type=button],[type=image]),textarea,select').length : 0;
        target = (this.type === 'search' || fields === 1) ? 'enter' : '';
    } else if ((this instanceof HTMLButtonElement || this instanceof HTMLInputElement) &&
               ['button', 'submit'].includes(this.type)) {
        kind = 'button';
    } else if (this instanceof HTMLDetailsElement || this.tagName === 'SUMMARY' ||
               this.matches('[role="tab"],[role="button"]')) {
        kind = 'button';
    } else {
        return null;
    }
    const control = [kind, label, target, options];
    if (expected === null) return control;
    if (JSON.stringify(control) !== JSON.stringify(expected)) return null;
    if (kind === 'link') return target;
    if (kind === 'type') return true;
    if (action === 'select') {
        if (!options.some(item => item[0] === option && !item[3])) return null;
        this.selectedIndex = option;
        this.dispatchEvent(new Event('input', {bubbles: true}));
        this.dispatchEvent(new Event('change', {bubbles: true}));
    } else {
        this.click();
    }
    return true;
}"""
_ROLES = {
    "link", "button", "tab", "combobox", "listbox", "DisclosureTriangle", "textbox", "searchbox",
}


def http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None
            and not any(ord(char) <= 32 or char == "\\" for char in url)
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class RetrievalAction:
    ref: str
    label: str
    control: list[Any]
    kind: str = "click"  # click, select, type or scroll
    option: int | None = None
    direction: str = ""


@dataclass(frozen=True)
class RetrievalObservation:
    url: str
    title: str
    text: str
    actions: tuple[RetrievalAction, ...]


_SCROLLS = (
    RetrievalAction("", "Scroll down", [], kind="scroll", direction="down"),
    RetrievalAction("", "Scroll up", [], kind="scroll", direction="up"),
)


async def observe(page: BrowserPage, read_chars: int) -> RetrievalObservation:
    snapshot = await page.snapshot()
    text, _ = await page.read_text(read_chars)
    actions: list[RetrievalAction] = []
    for element in snapshot.elements:
        if element.disabled or element.role not in _ROLES:
            continue
        try:
            control = await page._call_on(element.ref, _CONTROL, None, None, None)
        except BrowserError:
            # A page redraw can remove a control while the snapshot is read.
            continue
        if control is None:
            continue
        kind, label, target, options = control
        name = (element.name or label)[:_MAX_LABEL]
        if kind == "select":
            actions.extend(
                RetrievalAction(
                    element.ref, f"Select {label} in {name}", control, "select", index,
                )
                for index, label, _, selected in options if label and not selected
            )
        elif kind == "type":
            if name:
                enter = " and press Enter" if target == "enter" else ""
                actions.append(
                    RetrievalAction(element.ref, f"Type into {name}{enter}", control, "type")
                )
        elif name:
            description = f"Open {name} ({target[:160]})" if kind == "link" else f"Click {name}"
            actions.append(RetrievalAction(element.ref, description, control))
        if len(actions) >= _OFFERED:
            break
    return RetrievalObservation(
        snapshot.url, snapshot.title[:_MAX_LABEL], text, (*actions[:_OFFERED], *_SCROLLS),
    )


async def dispatch(page: BrowserPage, action: RetrievalAction, text: str = "") -> bool:
    page.policy.live(action.kind)
    if action.kind == "scroll":
        await page.scroll(action.direction)
        return True
    before = await page._loader()
    try:
        object_id = await page._object(action.ref)
    except BrowserError:
        # Resolving the old node cannot cause a page effect.
        return False
    page.policy.live(action.kind)
    result = await page._page("Runtime.callFunctionOn", {
        "objectId": object_id,
        "functionDeclaration": _CONTROL,
        "arguments": [{"value": value} for value in (action.control, action.kind, action.option)],
        "returnByValue": True,
    }, mutating=True)
    if "exceptionDetails" in result:
        raise BrowserError("the page action could not be confirmed")
    applied = result.get("result", {}).get("value")
    if isinstance(applied, str) and applied == action.control[2] and http_url(applied):
        await page.navigate(applied)
        return True
    if applied is not True:
        return False
    if action.kind == "type":
        await page.type_text(action.ref, text, submit=action.control[2] == "enter")
        return True
    await page.settle(before)
    return True
