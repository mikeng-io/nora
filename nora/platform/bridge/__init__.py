"""Shared platform-bridge wire contract (design/platform-bridges).

`nora.platform.bridge` holds the platform-agnostic pieces every bridge and the
app share: the v1 event envelope, canonical source identity, the durable
journal format, the Redis Streams dedupe/enqueue contract and the authenticated
actions/read API. Bridge processes for Signal and Discord are Python and import
this package directly; the WhatsApp (JS) and Instagram (Go) bridges implement
the same wire byte-for-byte — `nora.testing` carries parity fakes.
"""
