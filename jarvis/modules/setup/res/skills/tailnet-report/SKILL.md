---
name: tailnet-report
description: Use when the user wants to open a report on their own devices over their tailnet, for example "tailnet html로 보여줘", "tailnet 보고서", "show it as a tailnet page", covering status, architecture or deployment, or a session recap. Collects live evidence, freezes a fact sheet, builds the page with the visual-explainer skill, checks it in a real browser, and serves it tailnet-only with tailscale serve. Not for local-only HTML or public sharing.
---

# Tailnet Report

Turn verified facts about a system or a piece of work into one designed HTML page
that the user opens from their own devices over Tailscale. The value of the page
is that every number in it was measured, dated, and reconciled; the design comes
from visual-explainer.

Prerequisites:

- `visual-explainer` (`nicobailon/visual-explainer`, MIT) must be in your
  available skills list. If it is not, stop and tell the user to install it,
  for example `npx skills add nicobailon/visual-explainer#v0.12.0 -g`. Do not
  fall back to an undesigned page without saying so.
- The `tailscale` CLI on this machine. If `tailscale serve` is refused for your
  user, ask the user to run `sudo tailscale set --operator=$USER` once.

## 1. Collect evidence

- Read the sources the request names: session transcripts
  (`~/.claude/projects/*/<session-id>.jsonl`; extract user messages and the
  compaction summary with `jq` instead of reading the whole file), repositories
  (design docs, `git log`), and the running system (ssh, `docker ps`, `inspect`,
  `stats`, logs, live config).
- Probe from every vantage point the claim depends on, and name the one you
  actually used:
  - allowed and denied devices on the tailnet;
  - the internet through a CDN edge (public DNS over HTTPS, then
    `curl --resolve host:443:<edge-ip>`). This is a real outside path, but the
    source address is still this host's network;
  - this host calling its own public IP is not an outside vantage point
    (hairpin NAT). A true external check needs a device off this network, such
    as a phone on cellular.
  If a vantage point the claim depends on is not available, mark that claim not
  verified. Never claim a device you did not test from.
- Keep secret values out of tool output, `facts.md` and the page by filtering
  at the source. For env files list key names only. `docker inspect`,
  `docker compose config`, `systemctl show -p Environment` and transcripts can
  carry values too; print only key names from them, for example
  `docker inspect --format '{{range .Config.Env}}{{println (index (split . "=") 0)}}{{end}}' <name>`.
  Redact client IPs and tokens from log excerpts. The page is visible to the
  whole tailnet, including shared users.
- Note the local time of each measurement.

## 2. Freeze a fact sheet

Write every fact the page will use into one `facts.md` in the scratchpad: value,
source, and time. Then reconcile it before any HTML exists.

- Parts must add up to totals. Your own probes change logs and counters, so
  record whether each count was taken before or after them.
- Keep three states apart: verified, inferred (say from what), not verified.
  A missing success path (for example "no allowed request since the change")
  is a finding, not a gap to paper over.
- The page may use only facts from this sheet. New facts go into the sheet first.

## 3. Build the page

- Load `visual-explainer` and follow it fully: its style guide, diagram
  reference, `templates/page.html`, and its Before delivery checklist. Default
  page shape is Review or audit: verdict, evidence figures, risks, next steps.
- Prefer handing the build to a fresh subagent that gets only `facts.md`, the
  output path, and this section. It keeps the evidence work out of the page work
  and produces a less biased page. Review its report and its claims against
  `facts.md` before accepting it.
- Write body text in the user's language; keep identifiers, paths, commands and
  header names verbatim. Put the measurement time and vantage points in the
  header block.
- For a dense architecture diagram that must be validated element by element,
  `archify` can render that one figure; the page stays visual-explainer's.
- Output to a persistent, version-control-ignored folder in the current project,
  for example `.omc/artifacts/<slug>/index.html`, not to a session temp folder.

## 4. Check it in a browser

- Render with headless Chromium (`chromium`, `chromium-browser` or
  `google-chrome`) at 1280px and 390px wide, in light and dark
  (`--blink-settings=preferredColorScheme=1` is light, `=0` is dark), with
  `--virtual-time-budget=8000` so fonts and entrance motion settle.
- Confirm the narrow render is really 390px wide (the screenshot width, or
  `document.documentElement.clientWidth`). If headless clamps the window, use
  device emulation through CDP or Playwright instead.
- Look at the screenshots yourself. Fix overlapping or clipped labels, page-level
  horizontal overflow, unreadable contrast, and wrong theme colors.
- Grep the numbers on the page against `facts.md`.

## 5. Serve it on the tailnet only

1. Run `tailscale serve status`. Reuse the HTTPS port already in use, or 443
   when nothing is served. If `/<slug>` is already listed, pick another slug;
   `--set-path` silently replaces an existing path.
2. Serve the folder from loopback only on a free local port:
   `python3 -m http.server <port> --bind 127.0.0.1 --directory <dir>` (on
   Windows use the Python that `py -3` finds, not the Store alias).
   `tailscale serve` can serve a folder directly only as root or an operator
   allowed to sudo, so a loopback server is the default.
   - Linux: `systemd-run --user --unit=tailnet-report-<slug> python3 -m http.server ...`.
     The unit stops at logout unless `loginctl enable-linger` is on, and at
     reboot. Offer a real user unit for a long-lived page.
   - macOS and Windows: start it as a background process and tell the user it
     stops with the session or at reboot.
3. `tailscale serve --bg --https=<https-port> --set-path=/<slug> http://127.0.0.1:<port>`
4. `curl` the `https://<machine>.<tailnet>.ts.net:<https-port>/<slug>/` address
   and expect 200.

Never use `tailscale funnel`, a public tunnel, or a public artifact host for these
pages. To take one page down use
`tailscale serve --https=<https-port> --set-path=/<slug> off`; plain
`--https=<port> off` removes every path on that port.

## 6. Deliver

Reply with the URL, the measurement time, what was verified from where, what
is not verified, and the findings that need the user. Give the stop commands:
`tailscale serve --https=<https-port> --set-path=/<slug> off` and, on Linux,
`systemctl --user stop tailnet-report-<slug>`.
