---
name: design-lab
description: Use when the user wants multiple polished UI design variants, themes, or app mockups to compare in a gallery, especially with desktop/mobile interaction, design selection, feedback, or a home-server preview. 디자인 시안 비교, 테마별 풀 앱 체험, 시안 갤러리.
---

# Design Lab

Turn design alternatives into a gallery of complete, interactive app previews.

## 1. Set the comparison contract

Use the user's requested variant count; if absent, start with three. Reuse the
project's stack and assets. State assumptions about the product and sample data.
Choose visually distinct directions (layout, typography, spacing, color, imagery),
not recolors of one screen. Keep the same sample dataset and core task in every
variant so differences reflect design rather than missing features.

For a design-selection request, implement the preview once scope is clear;
a plan alone is not the deliverable. Ask only for missing information that
prevents meaningful progress. Preserve narrower scope explicitly requested by
the user, such as static concepts or a single screen.

## 2. Build the gallery and full previews

Give each variant a stable ID, name, thumbnail, short design rationale, and a link
to its complete app preview, for example `/design/a`. A gallery card or screenshot
is an entry point, not the whole variant. Allow return to the gallery and direct
loading of each preview URL. An iframe is optional; each variant must also work
at full size outside a small frame.

Each preview must demonstrate the same meaningful journey through completion
(for example browse → detail → choose date/time → confirm → demo result).
Implement visible navigation, filters, forms, dialogs, and feedback states needed
for that journey. Use realistic seeded data and clearly label simulated actions.
Use local demo state by default; connect real authentication, payments, messages,
or production writes only when the task authorizes them.

## 3. Make comparison usable

Support responsive desktop and mobile layouts. Keep cards and grids within the
available width even on narrow screens; do not hide overflow to conceal clipping.
Provide a working light/dark toggle
with readable colors in every variant. Give the gallery variant selection and
per-variant feedback fields. Show the storage scope: browser-local state is
enough by default; shared feedback needs an explicitly requested server store.
Retain inputs while switching previews. Include export or copy of feedback when
local state is used, so the user can return their choice and comments.

## 4. Verify and deliver

Run each variant and complete its core journey in a browser at desktop and mobile
sizes. Check light/dark modes, direct URLs, return navigation, selection, feedback,
and state retention. Check relevant empty, invalid, loading, and success states.
Resolve dead controls, horizontal overflow, unreadable text, and missing results.
A build or screenshot alone does not establish an interactive preview works.

Deliver one gallery URL, variant summaries, run/stop commands, and a concise list
of browser-verified journeys and simulated or unverified behavior. If LAN/tailnet
serving is requested, reuse the existing host/service convention and verify that
address. Bind only within the authorized audience; do not create a public tunnel
or claim another device was tested from a local-only check.
