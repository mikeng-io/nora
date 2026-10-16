"""A room's browser in Nora's computer, driven over CDP.

computerd serves each room's Chrome as one authenticated websocket
(`GET /v1/browser?scope=`), each frame a CDP message relayed to Chrome's
`--remote-debugging-pipe` (deploy/computer/computerd.py). This module is
the client half: `open_browser` connects, attaches to the room's page, and
yields a `BrowserPage` with the few actions the browse sub-agent is given.

What the model sees of a page is an accessibility snapshot: the interactive
elements, each with a short ref (`e1`, `e2`, ...), its role, its accessible
name and its value, the way agent-browser and Playwright's ARIA snapshots
do. Actions take a ref, never a selector or a pixel; a
screenshot draws the same refs on the page (Set-of-Marks), so an answer read
off the picture is still a ref. Refs live for one snapshot: a new snapshot
renumbers them, and an action on a ref the last snapshot did not give is
refused.

Everything here returns or raises plainly; the statuses the model is told are
decided by the caller (`nora.agent.browse`). No credential path exists.
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
    """The room's one page, through an attached CDP session.

    Nothing here runs in the page's own JavaScript world. Reads (the url, the
    title, the load state, the page text) and the per-element calls run in an
    ISOLATED world created for each document (`Page.createIsolatedWorld`, the
    approach Patchright takes): it shares the DOM but not the page's scripts,
    so a page can neither see them nor redefine what they read. Navigation is
    told by the main frame's `loaderId` changing, never by a marker left on the
    page, and the Set-of-Marks boxes are drawn onto the screenshot here, never
    into the page.
    """

    cdp: CdpConnection
    session_id: str
    target_id: str = ""
    refs: dict[str, int] = field(default_factory=dict)
    _world: tuple[str, int] | None = None

    async def _page(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.cdp.call(method, params, session_id=self.session_id)

    async def _frame(self) -> tuple[str, str]:
        """The main frame's id and its current document's `loaderId`."""
        frame = (await self._page("Page.getFrameTree"))["frameTree"]["frame"]
        return str(frame["id"]), str(frame.get("loaderId", ""))

    async def _context(self) -> int:
        """This document's isolated world, made on first use."""
        frame_id, loader = await self._frame()
        if self._world is not None and self._world[0] == loader:
            return self._world[1]
        got = await self._page("Page.createIsolatedWorld",
                               {"frameId": frame_id, "worldName": "nora"})
        self._world = (loader, int(got["executionContextId"]))
        return self._world[1]

    async def evaluate(self, expression: str) -> Any:
        """`expression`'s value, computed in the isolated world."""
        for attempt in (0, 1):
            try:
                got = await self._page("Runtime.evaluate", {
                    "expression": expression, "returnByValue": True,
                    "contextId": await self._context(),
                })
            except (BrowserError, KeyError, TypeError, ValueError):
                # The document changed under us (its world went with it).
                self._world = None
                if attempt:
                    raise BrowserError("the page could not be read") from None
                continue
            if "exceptionDetails" in got:
                raise BrowserError("the page's script failed")
            return got.get("result", {}).get("value")
        return None

    async def _loader(self) -> str:
        try:
            return (await self._frame())[1]
        except (BrowserError, KeyError, TypeError):
            return ""

    async def navigate(self, url: str) -> str:
        """Open `url` and wait for the page to finish loading (or for
        `LOAD_SECONDS`). Returns the URL the page ended on."""
        before = await self._loader()
        got = await self._page("Page.navigate", {"url": url})
        if got.get("errorText"):
            raise BrowserError(f"could not open the page: {got['errorText']}")
        # No `loaderId` back: a same-document navigation (a #fragment), so
        # there is no new document to wait for.
        new_document = bool(got.get("loaderId"))
        await self.settle(before if new_document else "", expect_navigation=new_document)
        return str(await self.evaluate("location.href") or url)

    async def settle(self, before: str = "", *, expect_navigation: bool = False) -> None:
        """Wait until the page is loaded. After an action that may navigate (a
        click, Enter, a submit, a select), the OLD document still reads
        `complete` for a moment, so first wait for the frame's `loaderId` to
        move off `before`: up to a second after an action, longer after an
        explicit navigation. A loader that never moves did not navigate."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + LOAD_SECONDS
        grace = loop.time() + (LOAD_SECONDS if expect_navigation else 1.0)
        if before:
            while loop.time() < min(grace, deadline):
                if await self._loader() not in ("", before):
                    break
                await asyncio.sleep(0.1)
        while loop.time() < deadline:
            with contextlib.suppress(BrowserError):
                if await self.evaluate("document.readyState") == "complete":
                    break
            await asyncio.sleep(0.25)
        await asyncio.sleep(0.5)  # late scripts that render after `load`

    async def _is_password(self, backend: int) -> bool:
        """Whether the element is an `<input type=password>`, read from the DOM,
        never from its label (a label may be in any language)."""
        try:
            node = (await self._page("DOM.describeNode", {"backendNodeId": backend}))["node"]
        except (BrowserError, KeyError, TypeError):
            return True  # unknown: masked
        attrs = node.get("attributes") or []
        pairs = dict(zip(attrs[0::2], attrs[1::2], strict=False))
        return str(pairs.get("type", "")).lower() == "password"

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
            if value and role in ("textbox", "searchbox") and await self._is_password(backend):
                value = "•••"
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
        got = await self._page("DOM.resolveNode", {
            "backendNodeId": self._node(ref), "executionContextId": await self._context(),
        })
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

    async def _targets(self) -> list[dict[str, Any]]:
        got = await self.cdp.call("Target.getTargets")
        return [t for t in got.get("targetInfos", []) if t.get("type") == "page"]

    async def _follow_new_tabs(self, known: set[str]) -> None:
        """A link that opens a new tab opens it unattached: this page would
        not change. Each new tab is closed and its address opened HERE, so the
        task goes on where the person clicking would have looked."""
        for target in await self._targets():
            if target.get("targetId") in known or target.get("targetId") == self.target_id:
                continue
            url = str(target.get("url") or "")
            with contextlib.suppress(BrowserError):
                await self.cdp.call("Target.closeTarget", {"targetId": target["targetId"]})
            if url.startswith(("http://", "https://")):
                await self.navigate(url)

    async def _viewport(self) -> tuple[float, float]:
        try:
            got = await self._page("Page.getLayoutMetrics")
            vp = got.get("cssVisualViewport") or got.get("visualViewport") or {}
            return float(vp["clientWidth"]), float(vp["clientHeight"])
        except (BrowserError, KeyError, TypeError, ValueError):
            return 1280.0, 800.0

    async def click(self, ref: str) -> None:
        node = self._node(ref)
        before = await self._loader()
        known = {str(t.get("targetId")) for t in await self._targets()}
        with contextlib.suppress(BrowserError):
            await self._page("DOM.scrollIntoViewIfNeeded", {"backendNodeId": node})
        try:
            box = await self._page("DOM.getBoxModel", {"backendNodeId": node})
            quad = box["model"]["content"]
            xs, ys = quad[0::2], quad[1::2]
        except (BrowserError, KeyError, TypeError, IndexError):
            # No layout box (an element drawn by script, or hidden): a DOM click.
            await self._call_on(ref, "function() { this.click(); }")
        else:
            # The box's centre, kept inside the part that is on screen: an
            # element taller than the window has its centre off it.
            width, height = await self._viewport()
            x = min(max(sum(xs) / 4, max(min(xs), 1.0)), min(max(xs), width - 1))
            y = min(max(sum(ys) / 4, max(min(ys), 1.0)), min(max(ys), height - 1))
            for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
                await self._page("Input.dispatchMouseEvent", {
                    "type": kind, "x": x, "y": y, "button": "left", "clickCount": 1,
                })
        await self.settle(before)
        await self._follow_new_tabs(known)

    async def type_text(self, ref: str, text: str, *, submit: bool = False) -> None:
        """Replace the field's text with `text`, as typing would: an input's
        value, or an editable element's text."""
        node = self._node(ref)
        await self._page("DOM.focus", {"backendNodeId": node})
        await self._call_on(ref, """function() {
            if ('value' in this) { this.value = ''; }
            else if (this.isContentEditable) { this.textContent = ''; }
            else { return; }
            this.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        await self._page("Input.insertText", {"text": text})
        if submit:
            await self.press("Enter")
        else:
            await asyncio.sleep(0.2)

    async def select(self, ref: str, option: str) -> str:
        """Choose the option whose visible text (or value) is `option`."""
        before = await self._loader()
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
        await self.settle(before)  # a change handler may reload the page
        return str(chosen)

    async def press(self, key: str) -> None:
        if key not in _KEYS:
            raise BrowserError(f"unknown key {key!r}")
        code, text, keycode = _KEYS[key]
        before = await self._loader()
        down: dict[str, Any] = {"type": "keyDown", "key": code, "code": code,
                                "windowsVirtualKeyCode": keycode}
        if text:
            down["text"] = text
        await self._page("Input.dispatchKeyEvent", down)
        await self._page("Input.dispatchKeyEvent",
                         {"type": "keyUp", "key": code, "code": code,
                          "windowsVirtualKeyCode": keycode})
        await self.settle(before)

    async def scroll(self, direction: str) -> None:
        dy = {"down": 1, "up": -1}.get(direction)
        if dy is None:
            raise BrowserError("scroll up or down")
        width, height = await self._viewport()
        await self._page("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": width / 2, "y": height / 2, "deltaX": 0,
            "deltaY": dy * round(height * 0.85),
        })
        await asyncio.sleep(0.4)

    async def read_text(self, max_chars: int) -> tuple[str, bool]:
        """The page's visible text, cut at `max_chars`."""
        text = str(await self.evaluate("document.body ? document.body.innerText : ''") or "")
        return text[:max_chars], len(text) > max_chars

    async def screenshot(self, *, marks: bool = True) -> bytes:
        """A JPEG of the viewport. With `marks`, each ref of the last snapshot
        that is on screen is boxed and labelled on the IMAGE (Set-of-Marks), so
        what the picture shows is answerable by ref; the page is not touched."""
        got = await self._page("Page.captureScreenshot", {"format": "jpeg", "quality": 70})
        data = got.get("data")
        if not isinstance(data, str):
            raise BrowserError("no screenshot came back")
        jpeg = base64.b64decode(data)
        if not marks or not self.refs:
            return jpeg
        boxes: list[tuple[str, float, float, float, float]] = []
        for ref, node in self.refs.items():
            try:
                quad = (await self._page("DOM.getBoxModel",
                                         {"backendNodeId": node}))["model"]["border"]
            except (BrowserError, KeyError, TypeError):
                continue
            xs, ys = quad[0::2], quad[1::2]
            boxes.append((ref, min(xs), min(ys), max(xs), max(ys)))
        return draw_marks(jpeg, boxes)


def draw_marks(jpeg: bytes, boxes: list[tuple[str, float, float, float, float]]) -> bytes:
    """`jpeg` with each `(ref, left, top, right, bottom)` boxed and labelled;
    boxes wholly off the image are skipped."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    image = Image.open(BytesIO(jpeg)).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for ref, left, top, right, bottom in boxes:
        if right < 0 or bottom < 0 or left > width or top > height:
            continue
        draw.rectangle((left, top, right, bottom), outline=(225, 29, 72), width=2)
        label_top = max(0.0, top - 12)
        draw.rectangle((left, label_top, left + 7 * len(ref) + 4, label_top + 12),
                       fill=(225, 29, 72))
        draw.text((left + 2, label_top), ref, fill=(255, 255, 255))
    out = BytesIO()
    image.save(out, format="JPEG", quality=70)
    return out.getvalue()


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_LABEL_CHARS else text[: MAX_LABEL_CHARS - 1] + "…"


@contextlib.asynccontextmanager
async def open_browser(*, base_url: str, token: str, scope: str,
                       timeout_seconds: float = CALL_SECONDS) -> AsyncIterator[BrowserPage]:
    """Connect to the room's browser through computerd and attach to its page.

    Raises `BrowserError` when computerd refuses (another client holds this
    room's browser, every browser slot is busy, the computer is off) or the
    page cannot be attached. A refusal is NOT asked again to learn its reason:
    any request to that address may start a browser (computerd answers a
    refusal with JSON instead of upgrading, and a second plain GET would run
    its start path again)."""
    ws_url = base_url.rstrip("/").replace("http://", "ws://", 1).replace(
        "https://", "wss://", 1) + f"/v1/browser?scope={scope}"
    headers = {"Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            ws = await session.ws_connect(ws_url, headers=headers, max_msg_size=64 * 1024 * 1024,
                                          heartbeat=30)
        except aiohttp.WSServerHandshakeError as exc:
            raise BrowserError("the computer refused the browser: it is busy, every "
                               f"browser slot is in use, or it is off ({exc.status})") from None
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise BrowserError(f"the computer could not be reached: {type(exc).__name__}") from None
        cdp = CdpConnection(ws)
        try:
            page = await _attach(cdp)
            yield page
        finally:
            await cdp.close()


async def _attach(cdp: CdpConnection) -> BrowserPage:
    """Attach to the room's page. Leftover tabs (a popup from an earlier task)
    are closed, so a task always starts on, and stays on, one page."""
    try:
        targets = [t for t in (await cdp.call("Target.getTargets")).get("targetInfos", [])
                   if t.get("type") == "page"]
        if targets:
            target_id = str(targets[0]["targetId"])
            for extra in targets[1:]:
                with contextlib.suppress(BrowserError):
                    await cdp.call("Target.closeTarget", {"targetId": extra["targetId"]})
        else:
            target_id = str((await cdp.call("Target.createTarget",
                                            {"url": "about:blank"}))["targetId"])
        attached = await cdp.call("Target.attachToTarget",
                                  {"targetId": target_id, "flatten": True})
        session_id = str(attached["sessionId"])
    except (KeyError, TypeError) as exc:
        raise BrowserError(f"the page could not be attached: {type(exc).__name__}") from None
    # Never `Runtime.enable`: pages can detect it, and it is the best-known CDP
    # automation tell; evaluation runs in an isolated world
    # (`BrowserPage._context`), which needs only `Page`.
    for domain in ("Page", "DOM", "Accessibility"):
        await cdp.call(f"{domain}.enable", session_id=session_id)
    return BrowserPage(cdp=cdp, session_id=session_id, target_id=target_id)
