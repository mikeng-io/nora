# n.example.net — Nora's link shortener

A Cloudflare Worker + KV behind `n.example.net`, Terraform-owned in this repo
(operator decision 2026-08-07). Self-hosted on purpose: a third-party
shortener would see every destination the group routes to.

## Deploy

```bash
cd deploy/shortener
export CLOUDFLARE_API_TOKEN=…          # zone + workers scope for example.net
terraform init
terraform apply \
  -var account_id=… \
  -var mint_token="$(openssl rand -hex 32)"
```

Put the same `mint_token` value into the repo `.env` as
`NORA_SHORTENER_TOKEN` (the Nora-side minting integration reads it there;
that integration lands separately, after this is applied and verified).

## Never commit

`terraform.tfstate*` (carries the mint token), `.terraform/`, `*.tfvars` —
all gitignored. State stays on this host, like every other secret.

## Verify after apply

```bash
curl -s https://n.example.net/           # → "nrtx link service"
curl -s -X POST https://n.example.net/api/links \
  -H "Authorization: Bearer $NORA_SHORTENER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.google.com/maps/dir/?api=1&origin=A&destination=B"}'
# → {"slug":"…","short_url":"https://n.example.net/…"} ; then GET it → 301.
```
