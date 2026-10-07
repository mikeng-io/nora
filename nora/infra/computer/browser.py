"""A room's browser in Nora's computer, driven over CDP (design/browser-runtime
00 §5, B2).

computerd serves each room's Chrome as one authenticated websocket
(`GET /v1/browser?scope=`), each frame a CDP message relayed to Chrome's
`--remote-debugging-pipe` (deploy/computer/computerd.py, B1). This module is
the client half: `open_browser` connects, attaches to the room's page, and
yields a `BrowserPage` with the few actions the browse sub-agent is given.

What the model sees of a page is an accessibility snapshot: the interactive
elements, each with a short ref (`e1`, `e2`, ...), its role, its accessible
name and its value, the way agent-browser and Playwright's ARIA snapshots
do (design 00 §5). Actions take a ref, never a selector or a pixel; a
screenshot draws the same refs on the page (Set-of-Marks), so an answer read
off the picture is still a ref. Refs live for one snapshot: a new snapshot
renumbers them, and an action on a ref the last snapshot did not give is
refused.

Everything here returns or raises plainly; the statuses the model is told are
decided by the caller (`nora.agent.browse`). No credential path exists yet
(the fill broker is B4).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import aiohttp

#: Roles a person acts on. A snapshot lists these, plus headings for bearings.
INTERACTIVE_ROLES = frozenset({
    "button", "link", "textbox", "searchbox", "combobox", "listbox", "option",
    "checkbox", "radio", "switch", "slider", "spinbutton", "menuitem",
    "menuitemcheckbox", "menuitemradio", "tab", "treeitem",
})
HEADING_ROLES = frozenset({"heading"})
#: A snapshot lists at most this many elements; the rest are counted.
MAX_SNAPSHOT_ELEMENTS = 150
#: An accessible name or value is cut at this many characters.
MAX_LABEL_CHARS = 80
#: How long a navigation may take to reach `complete` before the page is used
#: as it stands.
LOAD_SECONDS = 15.0
#: A CDP call that has not answered after this long is an error.
CALL_SECONDS = 30.0

_KEYS: dict[str, tuple[str, str, int]] = {
    "Enter": ("Enter", "\r", 13), "Tab": ("Tab", "", 9), "Escape": ("Escape", "", 27),
    "Backspace": ("Backspace", "", 8), "ArrowDown": ("ArrowDown", "", 40),
    "ArrowUp": ("ArrowUp", "", 38), "ArrowLeft": ("ArrowLeft", "", 37),
    "ArrowRight": ("ArrowRight", "", 39), "PageDown": ("PageDown", "", 34),
    "PageUp": ("PageUp", "", 33), "Home": ("Home", "", 36), "End": ("End", "", 35),
}
KEYS = frozenset(_KEYS)


class BrowserError(Exception):
    """The browser could not be used: refused, unreachable, or a CDP error."""


class RefError(BrowserError):
    """An action named a ref the last snapshot did not give."""


@dataclass(frozen=True)
class Element:
    ref: str
    role: str
    name: str
    value: str = ""
    checked: str = ""
    disabled: bool = False


@dataclass(frozen=True)
class Snapshot:
    url: str
    title: str
    elements: tuple[Element, ...]
    more: int = 0

    def render(self) -> str:
        """The snapshot as the model reads it: one line per element."""
        lines = [f"url: {self.url}", f"title: {self.title}"]
        for e in self.elements:
            line = f"[{e.ref}] {e.role} {json.dumps(e.name, ensure_ascii=False)}"
            if e.value:
                line += f" value={json.dumps(e.value, ensure_ascii=False)}"
            if e.checked:
                line += f" checked={e.checked}"
            if e.disabled:
                line += " disabled"
            lines.append(line)
        if self.more:
            lines.append(f"... {self.more} more elements not listed; scroll to reach them")
        return "\n".join(lines)


class CdpConnection:
    """One CDP websocket: numbered calls answered in any order, events
    dropped (the actions here poll instead of subscribing)."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws
        self._next = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type is not aiohttp.WSMsgType.TEXT:
                    break
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                waiter = self._pending.pop(data.get("id"), None) if "id" in data else None
                if waiter is not None and not waiter.done():
                    waiter.set_result(data)
        finally:
            for waiter in self._pending.values():
                if not waiter.done():
                    waiter.set_exception(BrowserError("the browser connection closed"))
            self._pending.clear()

    async def call(self, method: str, params: dict[str, Any] | None = None, *,
                   session_id: str | None = None, wait_s: float = CALL_SECONDS) -> dict[str, Any]:
        self._next += 1
        mid = self._next
        message: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session_id is not None:
            message["sessionId"] = session_id
        waiter: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[mid] = waiter
        try:
            await self._ws.send_str(json.dumps(message))
            answer = await asyncio.wait_for(waiter, wait_s)
        except (TimeoutError, aiohttp.ClientError, ConnectionError) as exc:
            raise BrowserError(f"{method}: {type(exc).__name__}") from None
        finally:
            self._pending.pop(mid, None)
        if "error" in answer:
            raise BrowserError(f"{method}: {answer['error'].get('message', 'error')}")
        result = answer.get("result")
        return result if isinstance(result, dict) else {}

    async def close(self) -> None:
        self._reader.cancel()
        with contextlib.suppress(Exception):
            await self._ws.close()


@dataclass
class BrowserPage:
    """The room's one page, through an attached CDP session."""

    cdp: CdpConnection
    session_id: str
    refs: dict[str, int] = field(default_factory=dict)

    async def _page(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.cdp.call(method, params, session_id=self.session_id)

    async def evaluate(self, expression: str) -> Any:
        got = await self._page("Runtime.evaluate",
                               {"expression": expression, "returnByValue": True})
        if "exceptionDetails" in got:
            raise BrowserError("the page's script failed")
        return got.get("result", {}).get("value")

    async def navigate(self, url: str) -> str:
        """Open `url` and wait for the page to finish loading (or for
        `LOAD_SECONDS`). Returns the URL the page ended on."""
        await self._mark()
        got = await self._page("Page.navigate", {"url": url})
        if got.get("errorText"):
            raise BrowserError(f"could not open the page: {got['errorText']}")
        await self.settle(expect_navigation=True)
        return str(await self.evaluate("location.href") or url)

    async def _mark(self) -> None:
        """Tag the current document, so `settle` can tell a navigation that
        replaced it from the same page still standing."""
        with contextlib.suppress(BrowserError):
            await self.evaluate("window.__nora_doc__ = true")

    async def settle(self, *, expect_navigation: bool = False) -> None:
        """Wait until the page is loaded. After an action that may navigate
        (a click, Enter, a submit), the OLD document still reads `complete`
        for a moment, so first wait up to a second for the tag `_mark` set to
        disappear (a new document); a document that keeps it did not navigate."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + LOAD_SECONDS
        grace = loop.time() + (LOAD_SECONDS if expect_navigation else 1.0)
        while loop.time() < min(grace, deadline):
            with contextlib.suppress(BrowserError):
                if await self.evaluate("window.__nora_doc__ === true") is not True:
                    break
            await asyncio.sleep(0.1)
        while loop.time() < deadline:
            with contextlib.suppress(BrowserError):
                if await self.evaluate("document.readyState") == "complete":
                    break
            await asyncio.sleep(0.25)
        await asyncio.sleep(0.5)  # late scripts that render after `load`

    async def snapshot(self) -> Snapshot:
        """The page's interactive elements and headings, numbered afresh."""
        tree = await self._page("Accessibility.getFullAXTree")
        url = str(await self.evaluate("location.href") or "")
        title = str(await self.evaluate("document.title") or "")
        self.refs = {}
        elements: list[Element] = []
        more = 0
        # Controls first, links after: a long page's links would otherwise
        # fill the list and push its search box or its buttons off it.
        nodes = [n for n in tree.get("nodes", []) if isinstance(n, dict)]
        def is_link(n: dict[str, Any]) -> bool:
            return (n.get("role") or {}).get("value") == "link"
        ordered = [n for n in nodes if not is_link(n)] + [n for n in nodes if is_link(n)]
        for node in ordered:
            if node.get("ignored"):
                continue
            role = str((node.get("role") or {}).get("value") or "")
            if role not in INTERACTIVE_ROLES and role not in HEADING_ROLES:
                continue
            backend = node.get("backendDOMNodeId")
            if not isinstance(backend, int):
                continue
            props = {p.get("name"): (p.get("value") or {}).get("value")
                     for p in node.get("properties", []) if isinstance(p, dict)}
            name = _cut(str((node.get("name") or {}).get("value") or ""))
            if not name and role in HEADING_ROLES:
                continue
            if len(elements) >= MAX_SNAPSHOT_ELEMENTS:
                more += 1
                continue
            ref = f"e{len(elements) + 1}"
            self.refs[ref] = backend
            value = str((node.get("value") or {}).get("value") or "")
            if props.get("protected") or (role == "textbox" and "password" in name.lower()):
                value = "•••" if value else ""
            checked = props.get("checked")
            elements.append(Element(
                ref=ref, role=role, name=name, value=_cut(value),
                checked=str(checked) if checked not in (None, False, "false") else "",
                disabled=bool(props.get("disabled")),
            ))
        return Snapshot(url=url, title=title, elements=tuple(elements), more=more)

    def _node(self, ref: str) -> int:
        node = self.refs.get(ref.strip().lstrip("@"))
        if node is None:
            raise RefError(f"{ref} is not on the last snapshot; take a new one")
        return node

    async def _object(self, ref: str) -> str:
        got = await self._page("DOM.resolveNode", {"backendNodeId": self._node(ref)})
        object_id = (got.get("object") or {}).get("objectId")
        if not object_id:
            raise BrowserError(f"{ref} is no longer on the page")
        return str(object_id)

    async def _call_on(self, ref: str, function: str, *args: Any) -> Any:
        got = await self._page("Runtime.callFunctionOn", {
            "objectId": await self._object(ref), "functionDeclaration": function,
            "arguments": [{"value": a} for a in args], "returnByValue": True,
        })
        if "exceptionDetails" in got:
            raise BrowserError(f"the action on {ref} failed on the page")
        return got.get("result", {}).get("value")

    async def click(self, ref: str) -> None:
        node = self._node(ref)
        await self._mark()
        with contextlib.suppress(BrowserError):
            await self._page("DOM.scrollIntoViewIfNeeded", {"backendNodeId": node})
        try:
            box = await self._page("DOM.getBoxModel", {"backendNodeId": node})
            quad = box["model"]["content"]
            x, y = sum(quad[0::2]) / 4, sum(quad[1::2]) / 4
        except (BrowserError, KeyError, TypeError, IndexError):
            # No layout box (an element drawn by script, or hidden): a DOM click.
            await self._call_on(ref, "function() { this.click(); }")
        else:
            for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
                await self._page("Input.dispatchMouseEvent", {
                    "type": kind, "x": x, "y": y, "button": "left", "clickCount": 1,
                })
        await self.settle()

    async def type_text(self, ref: str, text: str, *, submit: bool = False) -> None:
        """Replace the field's text with `text`, as typing would."""
        node = self._node(ref)
        await self._page("DOM.focus", {"backendNodeId": node})
        await self._call_on(ref, "function() { if ('value' in this) { this.value = ''; "
                                 "this.dispatchEvent(new Event('input', {bubbles: true})); } }")
        await self._page("Input.insertText", {"text": text})
        if submit:
            await self.press("Enter")
        else:
            await asyncio.sleep(0.2)

    async def select(self, ref: str, option: str) -> str:
        """Choose the option whose visible text (or value) is `option`."""
        chosen = await self._call_on(ref, """function(want) {
            if (!this.options) return null;
            const w = String(want).trim().toLowerCase();
            for (const o of this.options) {
              if (o.text.trim().toLowerCase() === w || o.value.toLowerCase() === w) {
                this.value = o.value;
                this.dispatchEvent(new Event('input', {bubbles: true}));
                this.dispatchEvent(new Event('change', {bubbles: true}));
                return o.text.trim();
              }
            }
            return null;
        }""", option)
        if chosen is None:
            raise BrowserError(f"{ref} has no option {option!r}")
        await self.settle()  # a change handler may navigate; settle notices
        return str(chosen)

    async def press(self, key: str) -> None:
        if key not in _KEYS:
            raise BrowserError(f"unknown key {key!r}")
        code, text, keycode = _KEYS[key]
        await self._mark()
        down: dict[str, Any] = {"type": "keyDown", "key": code, "code": code,
                                "windowsVirtualKeyCode": keycode}
        if text:
            down["text"] = text
        await self._page("Input.dispatchKeyEvent", down)
        await self._page("Input.dispatchKeyEvent",
                         {"type": "keyUp", "key": code, "code": code,
                          "windowsVirtualKeyCode": keycode})
        await self.settle()

    async def scroll(self, direction: str) -> None:
        dy = {"down": 1, "up": -1}.get(direction)
        if dy is None:
            raise BrowserError("scroll up or down")
        await self.evaluate(f"window.scrollBy(0, {dy} * Math.round(window.innerHeight * 0.85))")
        await asyncio.sleep(0.4)

    async def read_text(self, max_chars: int) -> tuple[str, bool]:
        """The page's visible text, cut at `max_chars`."""
        text = str(await self.evaluate("document.body ? document.body.innerText : ''") or "")
        return text[:max_chars], len(text) > max_chars

    async def screenshot(self, *, marks: bool = True) -> bytes:
        """A JPEG of the viewport. With `marks`, each ref of the last snapshot
        that is on screen is drawn as a numbered box first (Set-of-Marks) and
        removed after, so what the picture shows is answerable by ref."""
        drawn = False
        if marks and self.refs:
            boxes = []
            for ref, node in self.refs.items():
                try:
                    quad = (await self._page("DOM.getBoxModel",
                                             {"backendNodeId": node}))["model"]["border"]
                except (BrowserError, KeyError, TypeError):
                    continue
                xs, ys = quad[0::2], quad[1::2]
                boxes.append({"ref": ref, "x": min(xs), "y": min(ys),
                              "w": max(xs) - min(xs), "h": max(ys) - min(ys)})
            if boxes:
                await self.evaluate(_MARKS_DRAW % json.dumps(boxes))
                drawn = True
        try:
            got = await self._page("Page.captureScreenshot", {"format": "jpeg", "quality": 70})
        finally:
            if drawn:
                with contextlib.suppress(BrowserError):
                    await self.evaluate(_MARKS_CLEAR)
        data = got.get("data")
        if not isinstance(data, str):
            raise BrowserError("no screenshot came back")
        return base64.b64decode(data)


#: Absolutely positioned boxes in the page's own coordinates, one per ref on
#: screen; `pointer-events: none` so they never take a click.
_MARKS_DRAW = """(() => {
  const boxes = %s;
  const layer = document.createElement('div');
  layer.id = '__nora_marks__';
  layer.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647';
  for (const b of boxes) {
    if (b.y + b.h < 0 || b.y > innerHeight || b.x + b.w < 0 || b.x > innerWidth) continue;
    const box = document.createElement('div');
    box.style.cssText = `position:fixed;left:${b.x}px;top:${b.y}px;width:${b.w}px;` +
      `height:${b.h}px;outline:2px solid #e11d48;`;
    const tag = document.createElement('span');
    tag.textContent = b.ref;
    tag.style.cssText = 'position:absolute;left:0;top:-14px;font:bold 11px monospace;' +
      'background:#e11d48;color:#fff;padding:0 2px';
    box.appendChild(tag);
    layer.appendChild(box);
  }
  document.documentElement.appendChild(layer);
  return true;
})()"""
_MARKS_CLEAR = "document.getElementById('__nora_marks__')?.remove(), true"


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_LABEL_CHARS else text[: MAX_LABEL_CHARS - 1] + "…"


@contextlib.asynccontextmanager
async def open_browser(*, base_url: str, token: str, scope: str,
                       timeout_seconds: float = CALL_SECONDS) -> AsyncIterator[BrowserPage]:
    """Connect to the room's browser through computerd and attach to its page.

    Raises `BrowserError` when computerd refuses (another client holds this
    room's browser, every browser slot is busy, the computer is off) or the
    page cannot be attached."""
    ws_url = base_url.rstrip("/").replace("http://", "ws://", 1).replace(
        "https://", "wss://", 1) + f"/v1/browser?scope={scope}"
    headers = {"Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            ws = await session.ws_connect(ws_url, headers=headers, max_msg_size=64 * 1024 * 1024,
                                          heartbeat=30)
        except aiohttp.WSServerHandshakeError:
            # computerd answers a refusal as JSON instead of upgrading; read it.
            reason = "the computer refused the browser"
            with contextlib.suppress(Exception):
                async with session.get(ws_url, headers=headers) as resp:
                    body = await resp.json(content_type=None)
                    reason = str(body.get("reason") or body.get("status") or reason)
            raise BrowserError(reason) from None
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise BrowserError(f"the computer could not be reached: {type(exc).__name__}") from None
        cdp = CdpConnection(ws)
        try:
            targets = (await cdp.call("Target.getTargets")).get("targetInfos", [])
            page = next((t for t in targets if t.get("type") == "page"), None)
            if page is None:
                target_id = (await cdp.call("Target.createTarget",
                                            {"url": "about:blank"}))["targetId"]
            else:
                target_id = page["targetId"]
            attached = await cdp.call("Target.attachToTarget",
                                      {"targetId": target_id, "flatten": True})
            session_id = str(attached["sessionId"])
            # Never `Runtime.enable`: pages can detect it, and it is the
            # best-known CDP automation tell (design 00 §3); `Runtime.evaluate`
            # and `callFunctionOn` work without it.
            for domain in ("Page", "DOM", "Accessibility"):
                await cdp.call(f"{domain}.enable", session_id=session_id)
            yield BrowserPage(cdp=cdp, session_id=session_id)
        except (KeyError, TypeError) as exc:
            raise BrowserError(f"the page could not be attached: {type(exc).__name__}") from None
        finally:
            await cdp.close()
