# Runtime and isolation

## Control and data planes

The sandbox daemon is an HTTP server inside the microVM, reached only through
the deployment's restricted address and service authentication. It has no
client credentials of its own and no route to Nora's Postgres, memory
services, model gateway, object store, Docker socket or bridge accounts.

Nora's composition root supplies the runtime implementation and scope. The
model never chooses a scope, owner or daemon credential. The daemon runs
privileged enough to create scope users, while every command executes as the
scope's own unprivileged user and cannot read the service token.

A host lifecycle owner creates and supervises the VM and daemon separately
from tool traffic. It can stop, rebuild, back up and restore without becoming
a second runtime API. Browser lifecycle has its own controller and acceptance
boundary; it does not require the computer command path to manage Chrome.

## Runtime contract

The `SandboxRuntime` port runs commands, reads and writes files, performs
exact edits, lists state and moves bounded bytes in both directions. Its HTTP
implementation is independent of the VM technology; a fake passes the shared
contract suite.

A completed process with a non-zero exit is a completed result carrying that
exit code. A timeout, unreachable daemon, refused operation or malformed
result is unavailable. Output is bounded in the model view while full streams
can remain referenced by content key for audit.

The computer tools cover shell execution, direct file reads and writes,
unambiguous exact edits, inbox import, export and session inspection. Direct
file tools avoid shell quoting and silent `sed` misses. Long jobs use visible
terminal sessions rather than a hidden persistent kernel.

Document JSON, portal rendering and the document skill belong to the document
bundle. Browser task delegation and its scoped anonymous browser belong to the
browser-runtime bundle. Neither becomes a method on the generic shell runtime.

## Scope and paths

The canonical scope comes from the turn's unit or private-DM binding. Its full
value is domain-separated from other scope kinds and remains outside model
arguments. Paths resolve only inside that scope, with no traversal, absolute
escape or symlink trick granting another workspace.

A full-scope owner registry prevents two different scopes from sharing an
abbreviated OS identity. Existing users and directories must prove the same
ownership or refuse before mutation. Group, unit and DM scopes do not inherit
one another's fallback.

Each scope has its own working projects and imported inbox. There is no shared
writable scratch space. Unit-file access across scopes is a privileged,
code-bound read or explicit publication, not a general mount.

## Network and health

Default egress is denied except the explicitly admitted public path, with
private address ranges and Nora's services refused. The shared HTTP egress
gateway enforces destination policy without a direct fallback. Non-HTTP
protocols and browser traffic need their own evidence rather than assuming
that an HTTP rule covers them.

Third-party credentials, when required, are substituted by the host egress
mechanism for an intended destination. The VM sees no real key in its
environment or files. Browser login state is private scope data, not a master
credential.

Health reads authenticated HTTP bodies and expected state. It distinguishes
process down, daemon down, policy denial, expired host login and upstream
failure. A socket accepting a connection is not evidence that the daemon or
upstream works, and a missing optional integration is not unhealthy.
