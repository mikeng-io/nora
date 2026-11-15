// Managed identity policy for Keto v26.2.0. Computed permits are authoritative
// in strict mode; writers use grant_<permit> and deny_<permit> relations.
// Nora validates the single parent and resource containment before writing.
import { Namespace, Context } from "@ory/keto-namespace-types"

class Person implements Namespace {}

class Workspace implements Namespace {
  related: {
    member: Person[]
    admin: Person[]
    restricted: Person[]
    grant_speak: Person[]
    grant_view: Person[]
    grant_manage_members: Person[]
    grant_manage_grants: Person[]
    grant_delegate: Person[]
    deny_speak: Person[]
    deny_view: Person[]
    deny_manage_members: Person[]
    deny_manage_grants: Person[]
    deny_delegate: Person[]
  }

  permits = {
    blocked_speak: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_speak.includes(ctx.subject),

    speak: (ctx: Context) =>
      !this.permits.blocked_speak(ctx) &&
      (this.related.grant_speak.includes(ctx.subject)),

    blocked_view: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_view.includes(ctx.subject),

    view: (ctx: Context) =>
      !this.permits.blocked_view(ctx) &&
      (this.related.grant_view.includes(ctx.subject)),

    blocked_manage_members: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_manage_members.includes(ctx.subject),

    manage_members: (ctx: Context) =>
      !this.permits.blocked_manage_members(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.grant_manage_members.includes(ctx.subject)),

    blocked_manage_grants: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_manage_grants.includes(ctx.subject),

    manage_grants: (ctx: Context) =>
      !this.permits.blocked_manage_grants(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.grant_manage_grants.includes(ctx.subject)),

    blocked_delegate: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_delegate.includes(ctx.subject),

    delegate: (ctx: Context) =>
      !this.permits.blocked_delegate(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.grant_delegate.includes(ctx.subject)),

  }
}

class Unit implements Namespace {
  related: {
    workspace: Workspace[]
    member: Person[]
    admin: Person[]
    restricted: Person[]
    grant_speak: Person[]
    grant_view: Person[]
    grant_manage_members: Person[]
    grant_manage_grants: Person[]
    grant_delegate: Person[]
    deny_speak: Person[]
    deny_view: Person[]
    deny_manage_members: Person[]
    deny_manage_grants: Person[]
    deny_delegate: Person[]
  }

  permits = {
    blocked_speak: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_speak.includes(ctx.subject) ||
      this.related.workspace.traverse((w) => w.permits.blocked_speak(ctx)),

    speak: (ctx: Context) =>
      !this.permits.blocked_speak(ctx) &&
      (this.related.grant_speak.includes(ctx.subject) ||
        this.related.workspace.traverse((w) => w.permits.speak(ctx))),

    blocked_view: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_view.includes(ctx.subject) ||
      this.related.workspace.traverse((w) => w.permits.blocked_view(ctx)),

    view: (ctx: Context) =>
      !this.permits.blocked_view(ctx) &&
      (this.related.grant_view.includes(ctx.subject) ||
        this.related.workspace.traverse((w) => w.permits.view(ctx))),

    blocked_manage_members: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_manage_members.includes(ctx.subject) ||
      this.related.workspace.traverse((w) => w.permits.blocked_manage_members(ctx)),

    manage_members: (ctx: Context) =>
      !this.permits.blocked_manage_members(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.grant_manage_members.includes(ctx.subject) ||
        this.related.workspace.traverse((w) => w.permits.manage_members(ctx))),

    blocked_manage_grants: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_manage_grants.includes(ctx.subject) ||
      this.related.workspace.traverse((w) => w.permits.blocked_manage_grants(ctx)),

    manage_grants: (ctx: Context) =>
      !this.permits.blocked_manage_grants(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.grant_manage_grants.includes(ctx.subject) ||
        this.related.workspace.traverse((w) => w.permits.manage_grants(ctx))),

    blocked_delegate: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_delegate.includes(ctx.subject) ||
      this.related.workspace.traverse((w) => w.permits.blocked_delegate(ctx)),

    delegate: (ctx: Context) =>
      !this.permits.blocked_delegate(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.grant_delegate.includes(ctx.subject) ||
        this.related.workspace.traverse((w) => w.permits.delegate(ctx))),

  }
}

class Room implements Namespace {
  related: {
    unit: Unit[]
    member: Person[]
    admin: Person[]
    restricted: Person[]
    grant_speak: Person[]
    grant_view: Person[]
    grant_manage_members: Person[]
    grant_manage_grants: Person[]
    grant_delegate: Person[]
    deny_speak: Person[]
    deny_view: Person[]
    deny_manage_members: Person[]
    deny_manage_grants: Person[]
    deny_delegate: Person[]
  }

  permits = {
    blocked_speak: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_speak.includes(ctx.subject) ||
      this.related.unit.traverse((u) => u.permits.blocked_speak(ctx)),

    speak: (ctx: Context) =>
      !this.permits.blocked_speak(ctx) &&
      (this.related.member.includes(ctx.subject) ||
        this.related.admin.includes(ctx.subject) ||
        this.related.grant_speak.includes(ctx.subject) ||
        this.related.unit.traverse((u) => u.permits.speak(ctx))),

    blocked_view: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_view.includes(ctx.subject) ||
      this.related.unit.traverse((u) => u.permits.blocked_view(ctx)),

    view: (ctx: Context) =>
      !this.permits.blocked_view(ctx) &&
      (this.related.member.includes(ctx.subject) ||
        this.related.admin.includes(ctx.subject) ||
        this.related.grant_view.includes(ctx.subject) ||
        this.related.unit.traverse((u) => u.permits.view(ctx))),

    blocked_manage_members: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_manage_members.includes(ctx.subject) ||
      this.related.unit.traverse((u) => u.permits.blocked_manage_members(ctx)),

    manage_members: (ctx: Context) =>
      !this.permits.blocked_manage_members(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.grant_manage_members.includes(ctx.subject) ||
        this.related.unit.traverse((u) => u.permits.manage_members(ctx))),

    blocked_manage_grants: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_manage_grants.includes(ctx.subject) ||
      this.related.unit.traverse((u) => u.permits.blocked_manage_grants(ctx)),

    manage_grants: (ctx: Context) =>
      !this.permits.blocked_manage_grants(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.grant_manage_grants.includes(ctx.subject) ||
        this.related.unit.traverse((u) => u.permits.manage_grants(ctx))),

    blocked_delegate: (ctx: Context) =>
      this.related.restricted.includes(ctx.subject) ||
      this.related.deny_delegate.includes(ctx.subject) ||
      this.related.unit.traverse((u) => u.permits.blocked_delegate(ctx)),

    delegate: (ctx: Context) =>
      !this.permits.blocked_delegate(ctx) &&
      (this.related.admin.includes(ctx.subject) ||
        this.related.grant_delegate.includes(ctx.subject) ||
        this.related.unit.traverse((u) => u.permits.delegate(ctx))),

  }
}
