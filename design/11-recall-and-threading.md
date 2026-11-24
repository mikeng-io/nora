# Recall and threading

[Graph retrieval](cognition/retrieval.md) provides the relevant memory for a
pass. The Record supplies the room transcript and the source of quoted text.

## Reply references

The Record stores the reference to the parent message alongside a reply. A
parent resolves within the same conversation, through the platform's recorded
message reference. The prefill can grow its transcript window to include the
parent, up to the configured quote-window bound.

## Rendering

Threaded rows name the parent speaker when the parent resolves. Quoted text
comes from the resolved Record row. An unresolved parent supplies no quoted
body. The sender's quoted envelope cannot establish what someone else said.
