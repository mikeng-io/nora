"""Nora Drop.

Spec: docs/superpowers/specs/2026-08-12-nora-drop-design.md (§3/§6a describe
the ORIGINAL host-based routing design). **Superseded on routing shape** by an
approved revision, not reflected in that document's text: `drop.nora.example.net`
(a two-level subdomain) fails TLS at Cloudflare's edge because the zone's
Universal SSL wildcard only covers one subdomain level (`*.example.net`) without
the paid Advanced Certificate Manager add-on. The free fix collapses both
public capabilities onto the ONE already-working hostname (`nora.example.net`),
distinguished by PATH instead of by Host header. The spec document is left as
historical record of why host-based was tried first; this module implements
the revision, not the original text.

ONE process, TWO listeners, split by which socket a route is bound on rather
than by a second service (§3, §5a):

  internal_app (DROP_INTERNAL_PORT, default 8081) -- reachable ONLY as
  `drop:<port>` on the `nora` Docker network; no `ports:` entry in
  docker-compose.yml, no Tunnel ingress rule (§5a). The write API:
    POST /api/links      -- mint a slug (replicates deploy/shortener/'s own
                             contract exactly, §5)
    POST /api/artifacts  -- register artifact bytes + metadata; the concrete
                             implementation of `nora.domain.ports.ArtifactIngest`
                             (§7a; `nora.infra.drop.DropArtifactIngest` is the
                             Nora-side caller)

  public_app (DROP_PUBLIC_PORT, default 9102) -- the only listener with a host
  port publish (§6). Dispatch is by PATH PREFIX, not Host header -- there is
  only one public hostname (`nora.example.net`) now, so a Host-based split (the
  `ridgeline-drop`-derived pattern this app used to copy) has nothing left to
  distinguish. Each capability gets its own path prefix instead:

    GET /robots.txt   -- `User-agent: *\nDisallow: /` (task: "make sure
                         [drop] is NOT index[ed] by any search engine").
                         Advisory only -- only a COMPLIANT crawler checks it,
                         and only before it first fetches anything, so this
                         is one layer of three, not the defense. See
                         `hardening.py` for the other two (bot rejection by
                         User-Agent, `X-Robots-Tag` on every response), both
                         applied to `public_app` only via `middleware=`
                         below -- the internal listener (§5a) already has no
                         path to the internet, so nothing in `hardening.py`
                         applies there.
    GET /i/<slug>     -- 301 redirect to the minted URL. 404 for an unknown
                         slug.
    GET /drop/<slug>  -- renders an artifact: the stored bytes with their
                         stored `Content-Type`, a `Content-Disposition`
                         chosen by `rendering.py` (`inline` for a
                         `media_type` a browser already knows how to display
                         -- images, PDF, text, audio, video -- so opening the
                         link shows the artifact rather than downloading it;
                         `attachment` otherwise, with a sanitised,
                         extension-complete filename derived from the
                         artifact's own `title`), and
                         `X-Content-Type-Options: nosniff` (this app never
                         reads what a Compute job's bytes actually contain --
                         §7a's own named, still-open risk -- so nosniff at
                         least keeps a browser from re-interpreting a served
                         type against its stored one). 404 for an unknown
                         slug; 410 (Gone, not merely Not Found -- the
                         artifact WAS real and no longer is, the more
                         specific status for that case) once `expires_at`
                         has passed.

  The two path prefixes (`/i/`, `/drop/`) can never collide with each other
  or with `/health` since Starlette matches routes in registration order and
  none of the three prefixes is a subset of another.
"""

import asyncio
import os
import time
from typing import Any
from urllib.parse import urlsplit

import asyncpg
import boto3
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from artifacts import ArtifactStoreUnavailableError, PgRustfsArtifactStore
from auth import bearer_ok
from hardening import PublicListenerHardening
from links import LinkStoreUnavailableError, ValkeyLinkStore
from rendering import content_disposition

INTERNAL_PORT = int(os.environ.get("DROP_INTERNAL_PORT", "8081"))
PUBLIC_PORT = int(os.environ.get("DROP_PUBLIC_PORT", "9102"))

# The ONE public hostname these slugs are eventually served from (module
# docstring: both capabilities now live on `nora.example.net`, distinguished by
# path prefix, not Host header) -- NOT where this process listens; used to
# build the URLs `POST /api/links` and `POST /api/artifacts` hand back
# (this listener is INTERNAL, §5a, and never sees a public request's Host
# header, so the public base has to be told to it instead).
PUBLIC_BASE_URL = os.environ.get("DROP_PUBLIC_BASE_URL", "https://nora.example.net")


async def health(request: Request) -> JSONResponse:
    del request
    return JSONResponse({"status": "ok"})


# Standard, advisory-only crawler opt-out (module docstring) -- one layer of
# three; see `hardening.py` for the two that don't depend on a crawler's
# cooperation.
_ROBOTS_TXT_BODY = "User-agent: *\nDisallow: /\n"


async def robots_txt(request: Request) -> Response:
    del request
    return PlainTextResponse(_ROBOTS_TXT_BODY)


# ── internal: POST /api/links ────────────────────────────────────────────────

async def mint_link(request: Request) -> JSONResponse:
    """Replicates `deploy/shortener/worker/shortener.js`'s `POST /api/links`
    contract exactly (Nora Drop spec §5): bearer-authed, `{"url": "https://…"}`
    in, `{"slug", "short_url"}` out, https targets only.
    """
    if not bearer_ok(request, token=request.app.state.token):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    try:
        body: Any = await request.json()
    except Exception:  # noqa: BLE001 - any parse failure is a 400
        return JSONResponse(
            {"error": 'body must be {"url": "https://…"}'}, status_code=400,
        )
    url = body.get("url") if isinstance(body, dict) else None
    # A real parse, not a string prefix check -- shortener.js validates via
    # `new URL(target).protocol`, and a prefix match alone accepts malformed
    # URLs (an embedded space, no host) the Worker would reject. `urlsplit`
    # over `urlparse` because this only needs `.scheme`/`.netloc`, not the
    # params-handling `urlparse` adds for schemes that don't use it -- but
    # `urlsplit` itself is lenient about raw whitespace (it will happily
    # absorb a space into `netloc` rather than reject it), so a URL is also
    # rejected if it contains ANY whitespace: a well-formed URL never does
    # (RFC 3986 requires percent-encoding), so this catches exactly the class
    # of malformed input `urlsplit` alone lets through.
    has_no_whitespace = isinstance(url, str) and not any(c.isspace() for c in url)
    parsed = urlsplit(url) if has_no_whitespace else None
    if parsed is None or parsed.scheme != "https" or not parsed.netloc:
        return JSONResponse({"error": "https targets only"}, status_code=400)

    try:
        slug = await request.app.state.links.create(url=url)
    except LinkStoreUnavailableError:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    except RuntimeError:
        # MAX_MINT_ATTEMPTS collisions in a row (links.py) -- astronomically
        # unlikely, but a real refusal deserves a real status, not an
        # unhandled 500.
        return JSONResponse({"error": "could not allocate a slug"}, status_code=503)

    short_url = f"{PUBLIC_BASE_URL.rstrip('/')}/i/{slug}"
    return JSONResponse({"slug": slug, "short_url": short_url})


# ── internal: POST /api/artifacts ────────────────────────────────────────────

async def register_artifact(request: Request) -> JSONResponse:
    """The HTTP implementation of `ArtifactIngest.register`'s contract
    (`nora/domain/ports.py`, §7a): `multipart/form-data` in (`file`, `title`,
    `media_type`, optional `expires_at`), `{"artifact_id", "url"}` out --
    `ArtifactRegistration`'s exact shape, so `nora.infra.drop.
    DropArtifactIngest` on the other end needs no translation.

    **Why multipart, not a JSON body with base64.** Artifact bytes are real
    file content with no engineered size ceiling here (this app inherits no
    ceiling from `nora`'s own attachment path); base64 inflates the payload
    ~33% and, being embedded in a JSON value, forces the whole body to be
    buffered and decoded as one string before a single byte can be written
    anywhere. `Request.form()` streams a multipart body's file part instead,
    the same tradeoff `FilesystemBlobStore`'s docstring already makes for
    treating a 100 MiB video as an ordinary `bytes` value at this size class,
    and the shape most HTTP file-upload clients (including
    `aiohttp.FormData`, `DropArtifactIngest`'s own choice) speak natively.
    """
    if not bearer_ok(request, token=request.app.state.token):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - any multipart parse failure is a 400
        return JSONResponse(
            {"error": "body must be multipart/form-data with file/title/media_type"},
            status_code=400,
        )

    upload = form.get("file")
    title = form.get("title")
    media_type = form.get("media_type")
    expires_raw = form.get("expires_at")

    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"error": "missing file"}, status_code=400)
    if not isinstance(title, str) or not title:
        return JSONResponse({"error": "missing title"}, status_code=400)
    if not isinstance(media_type, str) or not media_type:
        return JSONResponse({"error": "missing media_type"}, status_code=400)

    expires_at: int | None = None
    if expires_raw not in (None, ""):
        try:
            expires_at = int(expires_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return JSONResponse({"error": "expires_at must be epoch ms"}, status_code=400)

    data = await upload.read()

    try:
        registration = await request.app.state.artifacts.register(
            data=data, title=title, media_type=media_type, expires_at=expires_at,
        )
    except ArtifactStoreUnavailableError:
        return JSONResponse({"error": "unavailable"}, status_code=503)

    url = f"{PUBLIC_BASE_URL.rstrip('/')}/drop/{registration.slug}"
    return JSONResponse({"artifact_id": registration.artifact_id, "url": url})


# ── internal: PATCH /api/artifacts/{artifact_id} ─────────────────────────────

async def update_artifact(request: Request) -> JSONResponse:
    """`PATCH /api/artifacts/{artifact_id}` — the HTTP implementation of
    `ArtifactIngest.update`'s contract (`nora/domain/ports.py`, Nora Compute
    spec §9): overwrites an existing artifact's bytes/metadata AT ITS EXISTING
    slug/URL, never mints a new one. Same multipart wire shape as
    `register_artifact` (this module's own docstring on why multipart, not
    JSON) plus the `artifact_id` path parameter naming which row to overwrite.
    """
    if not bearer_ok(request, token=request.app.state.token):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    artifact_id = request.path_params["artifact_id"]

    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - any multipart parse failure is a 400
        return JSONResponse(
            {"error": "body must be multipart/form-data with file/title/media_type"},
            status_code=400,
        )

    upload = form.get("file")
    title = form.get("title")
    media_type = form.get("media_type")
    expires_raw = form.get("expires_at")

    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"error": "missing file"}, status_code=400)
    if not isinstance(title, str) or not title:
        return JSONResponse({"error": "missing title"}, status_code=400)
    if not isinstance(media_type, str) or not media_type:
        return JSONResponse({"error": "missing media_type"}, status_code=400)

    expires_at: int | None = None
    if expires_raw not in (None, ""):
        try:
            expires_at = int(expires_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return JSONResponse({"error": "expires_at must be epoch ms"}, status_code=400)

    data = await upload.read()

    try:
        registration = await request.app.state.artifacts.update(
            artifact_id=artifact_id, data=data, title=title, media_type=media_type,
            expires_at=expires_at,
        )
    except ArtifactStoreUnavailableError:
        return JSONResponse({"error": "unavailable"}, status_code=503)

    if registration is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    url = f"{PUBLIC_BASE_URL.rstrip('/')}/drop/{registration.slug}"
    return JSONResponse({"artifact_id": registration.artifact_id, "url": url})


# ── public: path-routed redirect + render (module docstring) ────────────────

async def redirect_link(request: Request) -> Response:
    slug = request.path_params["slug"]
    try:
        url = await request.app.state.links.resolve(slug=slug)
    except LinkStoreUnavailableError:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    if url is None:
        return Response("not found", status_code=404)
    return RedirectResponse(url, status_code=301)


async def render_artifact(request: Request) -> Response:
    slug = request.path_params["slug"]
    try:
        row = await request.app.state.artifacts.get_by_slug(slug=slug)
    except ArtifactStoreUnavailableError:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    if row is None:
        return Response("not found", status_code=404)
    if row.expires_at is not None and int(time.time() * 1000) > row.expires_at:
        # Gone, not merely Not Found -- the artifact WAS real and no longer
        # is (module docstring); already correct as of task 3.
        return Response("expired", status_code=410)

    try:
        data = await request.app.state.artifacts.fetch_bytes(artifact_id=row.id)
    except ArtifactStoreUnavailableError:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    if data is None:
        return Response("not found", status_code=404)
    return Response(
        data,
        media_type=row.media_type,
        headers={
            "Content-Disposition": content_disposition(row.media_type, row.title),
            # §7a's own named, still-open risk: this app never reads what a
            # Compute job's bytes actually contain. nosniff at least stops a
            # browser from re-interpreting a served type against the one
            # stored for it.
            "X-Content-Type-Options": "nosniff",
        },
    )


# Two distinct Starlette apps, not one app mounted twice -- see module
# docstring. `/health` first on each so it can never be shadowed by a
# parametrised route. On `public_app`, the two capabilities are disjoint
# path prefixes (`/i/<slug>` vs. `/drop/<slug>`) rather than a Host-routed
# catch-all, so there's no ordering hazard between them either.
internal_app = Starlette(routes=[
    Route("/health", health),
    Route("/api/links", mint_link, methods=["POST"]),
    Route("/api/artifacts", register_artifact, methods=["POST"]),
    Route("/api/artifacts/{artifact_id}", update_artifact, methods=["PATCH"]),
])
# `middleware=[Middleware(PublicListenerHardening)]` -- bot rejection +
# X-Robots-Tag apply here ONLY, never on internal_app (§5a already has no
# path to the internet for that listener; see hardening.py's own docstring).
public_app = Starlette(
    routes=[
        Route("/health", health),
        Route("/robots.txt", robots_txt, methods=["GET"]),
        Route("/drop/{slug}", render_artifact, methods=["GET"]),
        Route("/i/{slug}", redirect_link, methods=["GET"]),
    ],
    middleware=[Middleware(PublicListenerHardening)],
)


def _asyncpg_dsn(database_url: str) -> str:
    """asyncpg's own DSN parser does not understand SQLAlchemy's `+asyncpg`
    driver marker -- strip it so this app can read the SAME `DATABASE_URL`
    shape `nora`'s own compose environment already uses (DEC-8: one name, one
    value, never a second convention for the same secret)."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _build_state() -> dict[str, Any]:
    from redis.asyncio import Redis

    database_url = os.environ["DATABASE_URL"]
    valkey_url = os.environ.get("VALKEY_URL", "redis://valkey:6379/0")
    rustfs_endpoint = os.environ.get("RUSTFS_ENDPOINT", "http://rustfs:9000")
    rustfs_bucket = os.environ.get("RUSTFS_BUCKET", "drop-artifacts")
    token = os.environ["NORA_DROP_TOKEN"]

    pg_pool = await asyncpg.create_pool(_asyncpg_dsn(database_url))
    redis = Redis.from_url(valkey_url)
    s3 = boto3.client(
        "s3",
        endpoint_url=rustfs_endpoint,
        aws_access_key_id=os.environ["RUSTFS_ROOT_USER"],
        aws_secret_access_key=os.environ["RUSTFS_ROOT_PASSWORD"],
    )

    artifact_store = PgRustfsArtifactStore(pg_pool=pg_pool, s3_client=s3, bucket=rustfs_bucket)
    await asyncio.to_thread(artifact_store.ensure_bucket)
    link_store = ValkeyLinkStore(redis)

    return {
        "token": token,
        "links": link_store,
        "artifacts": artifact_store,
        "_pg_pool": pg_pool,
        "_redis": redis,
    }


async def main() -> None:
    state = await _build_state()
    for app in (internal_app, public_app):
        for key, value in state.items():
            setattr(app.state, key, value)

    internal_server = uvicorn.Server(
        uvicorn.Config(
            internal_app, host="0.0.0.0", port=INTERNAL_PORT, log_level="info"  # noqa: S104
        )
    )
    public_server = uvicorn.Server(
        uvicorn.Config(
            public_app, host="0.0.0.0", port=PUBLIC_PORT, log_level="info"  # noqa: S104
        )
    )
    try:
        # One process, two sockets (module docstring) -- both servers run for
        # the lifetime of the container; if either exits the other is
        # cancelled too so the container doesn't keep running half-alive.
        await asyncio.gather(internal_server.serve(), public_server.serve())
    finally:
        await state["_pg_pool"].close()
        await state["_redis"].aclose()


if __name__ == "__main__":
    asyncio.run(main())
