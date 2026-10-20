# commonplate: blueprint

Where we are, what comes next, and in what order.

## 1. Where we are

| Piece | State |
|---|---|
| Tulsi Foods (private repo, Railway) | Live. Config refactored to `BIZ_*` env vars, zero visual diff. |
| Exporter (`tools/export_base.py` + `export_rules.py`) | Done, on branch `tools/export-base` in the private repo. Exact-text rules that fail loudly, forbidden-term scan. |
| commonplate base | Exported as one commit, no history. Boots, smoke test passes, forbidden scan and secrets scan clean. AGPL-3.0-or-later. |
| Public GitHub repo | You create it and push. Topics added. |

The rule that holds the whole plan together: **the private Tulsi repo is the source, commonplate is a generated artifact.** Fix things in Tulsi, re-run the exporter, publish the result. Never hand-edit commonplate in a way the exporter would overwrite.

## 2. Principles

1. Nothing business-specific in code. It lives in configuration or in the private repo.
2. Every integration is optional and inert until configured.
3. One process, SQLite, no build step. A kitchen with one laptop must be able to run it.
4. Country logic is a pack, never a conditional in core.
5. Transparency is a feature: customers and delivery partners can see what is charged, what is paid out and why.
6. No co-author or tool attribution on commits or PRs.

## 3. Phases

### Phase 0: ship the base (days)
- [ ] Create the public repo, push `main`, check the first CI-less boot from a fresh clone (`pip install -r requirements.txt`, `uvicorn`, `tests/smoke.py`).
- [ ] Enable private vulnerability reporting (needed for `SECURITY.md` to work).
- [ ] Add a screenshot or two to the README and a one-line About description.
- [ ] Add a GitHub Actions workflow: install, boot the app, run the smoke test, run the forbidden-term scan.
- [ ] Open the PR for `tools/export-base` in the private repo and merge.
- [ ] Add `make export` (or a script) that regenerates the base into a sibling directory.

**Done when:** a stranger can clone, run and see the sample kitchen in under five minutes.

### Phase 1: make it forkable (1–2 weeks)
- [ ] Replace the placeholder logo with a neutral mark and a documented way to swap it.
- [ ] Theme tokens in one CSS file (colours, fonts) driven by `BIZ_*` where sensible.
- [ ] Move remaining hardcoded copy (ETA text, pickup minutes, packing-fee wording) into config.
- [ ] Offline fonts (already used in Tulsi) shipped in the base so there is no Google Fonts call.
- [ ] Docs: `docs/setup.md`, `docs/configuration.md` (every env var), `docs/deploy-railway.md`, `docs/deploy-docker.md`.
- [ ] A seed command: `python -m app.seed` fills a demo menu and a few orders.
- [ ] Tests beyond the smoke test: order placement, totals, GST, coupon, delivery-zone fee.

**Done when:** a second real kitchen can deploy it with env vars and a menu file only.

### Phase 2: harden the core (2–3 weeks)
- [ ] Rate limiting on public endpoints; CSRF-safe admin; admin token rotation.
- [ ] Database migrations (a small versioned migration runner instead of ad-hoc `ALTER`).
- [ ] Backups: scheduled SQLite backup to a file or bucket, documented restore.
- [ ] Structured logging and a `/healthz` endpoint.
- [ ] Data retention: configurable deletion of old customer data, matching the privacy template.
- [ ] Audit log of admin actions (menu changes, price changes, refunds).
- [ ] Dependency pinning and automated update PRs.

### Phase 3: country packs (3–6 weeks)
Design first, then split. A pack is a directory that provides:

| Concern | India pack (`packs/in`) | UK pack (`packs/uk`) |
|---|---|---|
| Currency, formatting | INR, lakh grouping | GBP |
| Tax | GST slabs, invoice fields | VAT, allergen/FSA labelling |
| Payments | UPI deep links, cash | Card/Stripe links, cash |
| Couriers | Borzo, Porter, Shiprocket | Local courier APIs (to be chosen) |
| POS | Petpooja | To be chosen |
| Messaging | WhatsApp Cloud API templates | WhatsApp, SMS |
| Legal templates | IT Act / DPDP privacy, consumer rules | UK GDPR, consumer rights |
| Address | PIN code | Postcode |

Steps:
- [ ] Define the pack interface (`tax`, `payments`, `couriers`, `messaging`, `legal`, `address`) as small protocols in core.
- [ ] Move the existing India code behind it. Core keeps behaviour identical; verify with golden page snapshots as we did for the config refactor.
- [ ] Write the UK pack with the smallest useful set: VAT, postcode lookup, one payment link provider, UK privacy and refund templates.
- [ ] Packs ship in the same repo first (`packs/`); split into separate repos only if contributors need it.

**Done when:** `PACK=uk` boots and renders a UK-correct checkout; `PACK=in` is byte-identical to today.

### Phase 4: transparency for gig workers and customers (parallel, starts with research)
This is the part that makes the project different, and the part to get right rather than fast.

- [ ] **Customer side:** itemised receipt that separates food, packing, delivery fee, tax and any platform or service charge; shows what share goes to the rider.
- [ ] **Delivery partner side:** a rider-facing view of the job (pickup, drop, distance, payout, tip, wait time) before accepting; per-delivery earnings statement; export as CSV for their own records.
- [ ] **Evidence log:** append-only record of each delivery's timestamps, distance, fee charged and payout, exportable by the rider. This is the basis for pay disputes.
- [ ] **Open methodology:** publish how fees and payouts are computed, in plain language, in the repo.
- [ ] **Partner organisations:** talk to worker unions and collectives (India and UK) before designing further. Their needs decide the schema, not our guesses.
- [ ] Separate `worker-evidence` repo only if it grows beyond a module.

**Open questions:** how riders are identified when couriers are third-party APIs; what data we can legally hold about riders; whether kitchens want this exposed to riders by default.

### Phase 5: community (ongoing)
- [ ] Issue templates, labels (`good first issue`, `pack:in`, `pack:uk`), a short roadmap issue.
- [ ] A demo instance with the sample menu.
- [ ] Release tags and a changelog.
- [ ] Governance note: who can merge, how packs are accepted.
- [ ] Trademark check on the name `commonplate` before heavy promotion.

## 4. Tulsi (private) alongside

- Tulsi keeps its own branding, story pages, SEO content, menu data and integrations credentials.
- Each base release: re-run the exporter, then move Tulsi onto the new core only after a golden-snapshot diff shows no change.
- Remaining Tulsi-only cleanups: kitchen screen fallbacks, map links, flow JSON, ETA text, brand story.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Exporter rules drift as Tulsi changes | Rules fail loudly; run export in CI on the private repo. |
| Identity leaks into the public repo | Forbidden-term scan plus secrets scan on every export, before every push. |
| Pack split breaks India behaviour | Golden snapshots and the smoke test must pass before any merge. |
| Rider data and privacy law | Collect the minimum; get legal review before storing rider identity. |
| Scope creep | Phases are ordered; nothing in Phase 3 starts before Phase 1 is done. |

## 6. Suggested next three actions

1. Push the public repo and add the CI workflow (Phase 0).
2. Merge the exporter PR in the private repo and add a one-command regenerate.
3. Write `docs/configuration.md` from the actual env vars (Phase 1), since it also drives the pack interface design.
