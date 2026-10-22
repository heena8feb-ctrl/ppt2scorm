# Security

## Reporting a problem

Please don't open a public issue for a security problem. Use GitHub's
[private vulnerability reporting](https://github.com/heena8feb-ctrl/ppt2scorm/security/advisories/new) instead.

## Before you host a copy for other people

| Do this | Why |
|---|---|
| Set `PPT2SCORM_APP_PASSWORD` | Without it, anyone with the link uses your API keys and server. For company-wide use, put the app behind your single sign-on (Cloudflare Access, Google IAP, Azure Entra ID) instead. |
| Set `PPT2SCORM_CLIENT_IP_HEADER` or `PPT2SCORM_PROXY_HOPS` for your host | Per-visitor limits need the visitor's real IP. Open `/api/whoami` and check it shows your own public IP. |
| Keep `PPT2SCORM_GLOBAL_DAILY_LIMIT` low | A hard daily ceiling on your AI keys, whoever is visiting. |
| Set `PPT2SCORM_BRAND_SAVING=0` on public servers | Saved brand kits are shared by everyone using the server. |
| Never set `PPT2SCORM_ALLOW_PRIVATE_FETCH` | It exists only for automated tests. |
| Keep confidential decks off free AI tiers | Free tiers may use what you send to improve their models. Use a paid key, Google Vertex AI, your company's approved provider, or Basic mode. |

`render.yaml` already applies these settings for Render; you only add the password and keys.

## What the app does to protect itself

- **Usage limits.** AI runs on the server's keys are limited per visitor per day and in total per day.
  Uploads and look-ups are limited per visitor per 10 minutes, the queue has a ceiling, and jobs and drafts
  have a disk budget. Jobs are deleted after 2 hours.
- **Visitor IPs** come only from sources visitors can't forge (a header your host sets, or a fixed number of
  proxy hops), never from the left of `X-Forwarded-For`.
- **Optional password** with signed, HttpOnly, SameSite cookies, a lockout after repeated wrong guesses and
  no open redirects.
- **Uploaded decks** are checked before opening: decks that would unpack to more than 400 MB or have an
  extreme compression ratio ("zip bombs") are refused. Pictures over about 89 megapixels are skipped,
  and SVG pictures are never copied out of decks (they can carry scripts). Uploaded pictures and logos are
  re-saved by the app, and fonts must be real fonts.
- **Website brand detection** only reaches public internet addresses, checks every redirect, and connects to
  the exact address it checked, so it can't be turned against internal systems (including DNS rebinding).
  Certificates are still verified.
- **Browser protection.** A strict Content Security Policy (no inline or third-party scripts), `nosniff`,
  same-origin referrers, no framing by other sites, and files served under a sandbox policy.
- **AI output** must match the course schema and is always shown as text, never as HTML. The prompt tells
  the model to treat deck text as content, not instructions.
- **Keys** pasted by people are used for that request only and never stored or logged.
- **Supply chain.** The Docker image installs exact versions from `requirements.lock`, runs as a non-root user,
  and Dependabot, CodeQL and `pip-audit` run on GitHub.

## Settings

| Variable | Default | |
|---|---|---|
| `PPT2SCORM_APP_PASSWORD` | none | Password needed to use the app |
| `PPT2SCORM_CLIENT_IP_HEADER` | none | Header with the visitor's IP set by your host, e.g. `cf-connecting-ip` |
| `PPT2SCORM_PROXY_HOPS` | 0 | Proxies in front of the app that append to `X-Forwarded-For` |
| `PPT2SCORM_DAILY_LIMIT` | 10 | AI runs per visitor per day on the server's keys |
| `PPT2SCORM_GLOBAL_DAILY_LIMIT` | 100 | AI runs per day on the server's keys for everyone |
| `PPT2SCORM_ACTIONS_PER_10MIN` | 30 | Uploads and look-ups per visitor per 10 minutes |
| `PPT2SCORM_MAX_QUEUE` | 6 | Conversions running or waiting |
| `PPT2SCORM_MAX_DISK_MB` | 2000 | Space for jobs and drafts |
| `PPT2SCORM_MAX_MB` | 50 | Largest upload |
| `PPT2SCORM_MAX_UNPACKED_MB` | 400 | Largest deck once unpacked |
| `PPT2SCORM_BRAND_SAVING` | 1 | 0 = brand kits can't be saved on the server |
| `PPT2SCORM_MAX_BRAND_KITS` | 50 | Most kits that can be saved |
| `PPT2SCORM_FETCH_VIA_PROXY` | 0 | 1 = website look-ups use the system proxy (office networks; laptop use only) |
