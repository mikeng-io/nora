# n.example.net — Nora's link shortener: one Worker, one KV namespace, one
# custom domain. Owned IN THIS REPO (operator decision 2026-08-07) so the
# infrastructure is versioned next to the thing that mints the links.
#
# Apply needs two things that are NEVER committed:
#   CLOUDFLARE_API_TOKEN  in the environment (zone + workers scope for example.net)
#   mint_token            via -var or a gitignored *.tfvars — the same value
#                         goes in Nora's .env as NORA_SHORTENER_TOKEN.
# State (terraform.tfstate) carries secrets and is gitignored; see README.md.

terraform {
  required_providers {
    cloudflare = {
      source = "cloudflare/cloudflare"
      # Pinned to the v4 schema (worker_script / worker_domain resource names).
      # v5 renamed these with breaking changes — upgrade deliberately, not by
      # a floating constraint.
      version = "~> 4.52"
    }
  }
}

provider "cloudflare" {}

variable "account_id" {
  type        = string
  description = "Cloudflare account id (dash: Workers & Pages → account details)."
}

variable "zone" {
  type    = string
  default = "example.net"
}

variable "hostname" {
  type    = string
  default = "n.example.net"
}

variable "mint_token" {
  type        = string
  sensitive   = true
  description = "Bearer token the mint API requires; mirror into Nora's .env."
}

data "cloudflare_zone" "zone" {
  name = var.zone
}

resource "cloudflare_workers_kv_namespace" "links" {
  account_id = var.account_id
  title      = "nora-links"
}

resource "cloudflare_worker_script" "shortener" {
  account_id = var.account_id
  name       = "nora-shortener"
  content    = file("${path.module}/worker/shortener.js")
  module     = true

  kv_namespace_binding {
    name         = "LINKS"
    namespace_id = cloudflare_workers_kv_namespace.links.id
  }

  secret_text_binding {
    name = "MINT_TOKEN"
    text = var.mint_token
  }
}

resource "cloudflare_worker_domain" "shortener" {
  account_id = var.account_id
  zone_id    = data.cloudflare_zone.zone.id
  hostname   = var.hostname
  service    = cloudflare_worker_script.shortener.name
}

output "short_base" {
  value = "https://${var.hostname}/"
}
