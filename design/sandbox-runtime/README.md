# Sandbox runtime

Nora's trusted control plane stays outside the computer. Model-influenced
commands, files and browser actions run inside a microVM that cannot reach her
memory stores, credentials, transport bridges or control sockets.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fsandbox-runtime%2F%22)

## Parts

- **The sandbox and computer daemon** provide a small authenticated runtime API
  for commands, files, state and lifecycle. [Runtime](runtime.md) defines its
  network and ownership boundaries.
- **Scoped workspaces and users** isolate each unit or canonical private-DM
  scope with separate operating-system ownership, not just a directory name.
- **The runtime port and tools** run commands, read, write, edit, import,
  export, inspect sessions and return typed outcomes. Documents and browser
  execution have their own owners rather than being reimplemented here.
- **Audit and sessions** record every effect and keep observed work visible
  across stateless turns. [Sessions](sessions.md) defines state, leases and
  expiry.
- **File custody and backup** move bytes only through Nora's checked export
  and import paths, with private manifests and restorable host archives.
  [Files](files.md) defines that lifecycle.

## Terms and promises

Content, not the person asking, is the untrusted party. A link, attachment,
webpage or generated script receives no Nora credential and no route to her
internal services. The VM calls nothing of Nora's; Nora calls the daemon.

Each scope gets its own Unix user and restrictive workspace. One scope cannot
read another's files through an ordinary command. A kernel or root escape is
outside that claim, which is why the VM, not the directory, is the security
boundary against Nora's side.

Exports are validated and hashed by Nora, never by the VM's claim. Published
files and documents follow their respective publication paths; the sandbox
does not receive a publication credential or public renderer.

## Change and failure

The VM and image can be replaced without moving the runtime port. Lifecycle
automation and browser lifecycle may change independently. Any alternate
executor must preserve scoped ownership, egress denial, typed outcomes and the
complete audit trail.

An unavailable daemon, timeout, missing file, refused path or failed backup is
not empty success. A scope-owner collision or unknown pre-existing owner
refuses before binding. Lifecycle health uses authenticated HTTP and real
responses, never a successful TCP connect as proof of service.
