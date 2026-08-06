# AM4 Funnel demo lane

Short-lived public demos on AM4, for putting a page in front of community
creators and collecting feedback fast. Zero incremental cost, trusted TLS, and
**every demo expires on its own**.

Tooling: [`scripts/am4-funnel-demo.sh`](scripts/am4-funnel-demo.sh), installed on
AM4 at `/usr/local/bin/am4-funnel-demo`.

## Why this exists

The manual version of this — stage a directory, run an nginx container on a free
loopback port, add a Funnel path, remember to take it down 48 hours later — was
done by hand twice. The second time, the reminder to tear down the *first* one
was still sitting in an agent memory file, past its due date. Demos that depend
on a human remembering become permanent public hosting by accident.

So the lane enforces what the manual version only hoped for:

- **Expiry is mandatory.** `publish` always arms a transient systemd timer. There
  is no way to publish something that never expires.
- **`tailscale funnel reset` is never invoked.** It is not path-scoped; it would
  destroy every public route on this host at once — the gallery, the IRC portal,
  the member guide, The Lounge, and the IRC TLS listener. Routes come down one at
  a time with `--set-path=<p> off`.
- **Reserved routes are refused by name.** `/`, `/join`, `/guide`, `/lab` cannot
  be taken by a demo.
- **Nothing is exposed until the backend answers.** The publish path polls
  `127.0.0.1:<port>` for HTTP 200 and aborts — cleaning up after itself — before
  touching Funnel if the page does not serve.
- **It will not clobber routes it does not own.** A path published by some other
  process is refused, not overwritten.

## Usage

Source is a file or directory **on AM4**. A single file is published as
`index.html`; a directory must contain one.

```bash
scp ./mypage.html am4:/tmp/
```

```bash
ssh am4 "sudo am4-funnel-demo publish mydemo /tmp/mypage.html --ttl 48h"
```

That prints the public URL and the exact wall-clock expiry. Then:

```bash
ssh am4 "am4-funnel-demo list"
```

```bash
ssh am4 "sudo am4-funnel-demo extend mydemo 24h"
```

```bash
ssh am4 "sudo am4-funnel-demo unpublish mydemo"
```

`unpublish` is idempotent and is exactly what the expiry timer runs, so the
manual and automatic teardown paths are the same code.

## What it creates

| Thing | Location |
|---|---|
| Files | `/opt/am4-demos/<slug>/` |
| Container | `am4demo-<slug>` (nginx:alpine, `127.0.0.1:<port>` only) |
| Port | first free in 9020–9059 |
| Funnel route | `/<slug>` on `:443` |
| Expiry timer | `am4-demo-expire-<slug>.timer` |
| State | `/var/lib/am4-funnel-demo/<slug>.{port,published,ttl}` |

Serving is read-only. Any "save" in a demo page is client-side (Blob download,
`localStorage`); nothing writes back to AM4, and one viewer's state never
reaches another's.

## Long-lived routes on this host — do not disturb

These are **not** demos and are not managed by this script:

| Route | Backend | Owner |
|---|---|---|
| `:443` `/` | `127.0.0.1:8190` | image gallery / workbench (Caddy) |
| `:443` `/join` `/guide` `/lab` | `127.0.0.1:9010` | irc community portal |
| `:10000` | `127.0.0.1:9000` | The Lounge |
| `:8443` TCP | `127.0.0.1:6668` PROXY v2 | Ergo IRC |

Their runbook is `C:\work\irc\docs\AM4.md`.

## Notes

- The public hostname is `am4.<tailnet>.ts.net`; get the literal value from
  `tailscale status` on the box. Do not paste it into the **baseline** repo,
  which is public.
- Publishing puts the page on the open internet. It is unlisted, not private —
  confirm the content is safe to expose, and check for credentials, emails, and
  personal data before publishing.
- Demo pages that reference external assets (web fonts, CDNs) will cause viewer
  browsers to hit those third parties. Prefer self-contained pages.
- Ports 9020–9059 are reserved for this lane; do not bind long-lived services
  there.
