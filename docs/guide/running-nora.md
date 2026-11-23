# Running Nora

Nora is self-hosted. This page explains what running her involves; the
[deployment guide](../../deploy/README.md) has the steps.

## What you need

- **A Linux host** with Docker Compose. Nora, her database and her supporting
  services run there together.
- **An AI model service** that speaks the common OpenAI-style interface. It
  can be a hosted provider or your own.
- **At least one messaging account** for her on a supported app.
- **Optional services** for the capabilities you want: search, maps, image and
  video generation, voice, a computer workspace.

## What you decide

Running Nora is mostly deciding four things, each kept in its own file on
your host and never in version control:

| decision | answers |
|---|---|
| **settings** | which services and models she uses, and which features are on |
| **identity** | who the people are, and which accounts belong to whom |
| **policy** | which rooms she may join, their circles, and what each room allows |
| **secrets** | the credentials for the services above |

Policy allows nothing by default. You name each room and circle on purpose.

## Her personality

Her voice and conduct are plain text that you can read and edit. An edit takes
effect on her next turn, without a restart, and a broken edit keeps the last
good version. She learns about people and about her own practice, but she
never rewrites her personality; only you do.

## Keeping her running

- **Releases** are built automatically from the project's history. A
  deployment can apply them on its own, or you can apply them by hand.
- **Health, metrics and traces** show what she did on every turn and why,
  including what she was given, what she decided and what it cost.
- **Backups and rollback** follow the [runbook](../../docs/runbook.md), which
  also covers what to do when she goes silent.

## What to expect

Nora is experimental. Interfaces and requirements change between releases,
and there is no support promise. Plan to read release notes, keep backups and
start with a small set of rooms.
