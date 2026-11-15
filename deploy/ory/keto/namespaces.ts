// Nora's managed-mode permission model (design/managed-identity-access/authorization.md).
// OPL for Ory Keto v26.2.0 — declared source for the model the design describes.
//
// Model:
//   - Person is the only subject. Nora writes tuples only after validating real
//     containment: a Room's `unit` and a Unit's `workspace` are Nora's own
//     topology facts, never something a tuple proves.
//   - `member` is roster membership at that scope and nothing more — it confers
//     no management permit anywhere.
//   - `admin` is the full scoped operator; `manage_members`, `manage_grants`
//     and `delegate` are also separate relations so one management power can be
//     delegated without the rest.
//   - `restricted` is an explicit deny. Deny always wins: every permit first
//     evaluates `blocked`, which holds at the scope and inherits downward
//     through containment, exactly as grants inherit downward.
//
// Task 1 contract artifact: source-verified against the v26.2.0 OPL reference,
// not yet compiled by a running Keto — the service integration task runs the
// real `opl/syntax/check` and effective-permission cases.

import { Namespace, Context } from "@ory/keto-namespace-types"

class Person implements Namespace {}

class Workspace implements Namespace {
  related: {
    member: Person[]
    admin: Person[]
    restricted: Person[]
    manage_members: Person[]
    manage_grants: Person[]
    delegate: Person[]
  }

  permits = {
    blocked: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject),

    manage_members: (ctx: Context) =>
      !this.permits.blocked(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.manage_members.includes(ctx.subject)),

    manage_grants: (ctx: Context) =>
      !this.permits.blocked(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.manage_grants.includes(ctx.subject)),

    delegate: (ctx: Context) =>
      !this.permits.blocked(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.delegate.includes(ctx.subject)),
  }
}

class Unit implements Namespace {
  related: {
    workspace: Workspace[]
    member: Person[]
    admin: Person[]
    restricted: Person[]
    manage_members: Person[]
    manage_grants: Person[]
    delegate: Person[]
  }

  permits = {
    blocked: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.workspace.traverse((w) => w.permits.blocked(ctx)),

    manage_members: (ctx: Context) =>
      !this.permits.blocked(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.manage_members.includes(ctx.subject) ||
        this.related.workspace.traverse((w) => w.permits.manage_members(ctx))),

    manage_grants: (ctx: Context) =>
      !this.permits.blocked(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.manage_grants.includes(ctx.subject) ||
        this.related.workspace.traverse((w) => w.permits.manage_grants(ctx))),

    delegate: (ctx: Context) =>
      !this.permits.blocked(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.delegate.includes(ctx.subject) ||
        this.related.workspace.traverse((w) => w.permits.delegate(ctx))),
  }
}

class Room implements Namespace {
  related: {
    unit: Unit[]
    member: Person[]
    admin: Person[]
    restricted: Person[]
    manage_members: Person[]
    manage_grants: Person[]
    delegate: Person[]
  }

  permits = {
    blocked: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.unit.traverse((u) => u.permits.blocked(ctx)),

    speak: (ctx: Context) =>
      !this.permits.blocked(ctx) &&
      (this.related.member.includes(ctx.subject) ||
        this.related.admin.includes(ctx.subject)),

    view: (ctx: Context) =>
      !this.permits.blocked(ctx) &&
      (this.related.member.includes(ctx.subject) ||
        this.related.admin.includes(ctx.subject)),

    manage_members: (ctx: Context) =>
      !this.permits.blocked(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.manage_members.includes(ctx.subject) ||
        this.related.unit.traverse((u) => u.permits.manage_members(ctx))),

    manage_grants: (ctx: Context) =>
      !this.permits.blocked(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.manage_grants.includes(ctx.subject) ||
        this.related.unit.traverse((u) => u.permits.manage_grants(ctx))),

    delegate: (ctx: Context) =>
      !this.permits.blocked(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.delegate.includes(ctx.subject) ||
        this.related.unit.traverse((u) => u.permits.delegate(ctx))),
  }
}
