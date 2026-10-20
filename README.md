# commonplate

Direct ordering for small kitchens: menu, cart, UPI/cash checkout, a kitchen screen,
order tracking, WhatsApp notifications and courier dispatch. FastAPI, Jinja2 and SQLite,
one process, no build step.

Licensed AGPL-3.0-or-later. If you run a modified version as a service, you must offer
your users its source.

## Run it

```
pip install -r requirements.txt
cp .env.example .env        # set ADMIN_TOKEN at least
uvicorn app.main:app --reload
python tests/smoke.py       # in another shell, with the app running
```

Open http://127.0.0.1:8000. The kitchen screen is `/kitchen`, the admin `/admin`.

## Make it yours

- **Business details**: `BIZ_*` environment variables (`BIZ_NAME`, `BIZ_PHONE`, `BIZ_STREET`,
  `BIZ_CITY`, hours and so on). Defaults are in `app/business.py`.
- **Menu**: `data/menu.json` (a sample is included).
- **Pricing and delivery zones**: `app/config.py`, overridable by environment variables.
- **Pages**: `app/templates/about.html`, `privacy.html` and `refund-cancellation-policy.html`
  are templates to rewrite. Have the policies reviewed for your jurisdiction.
- **Integrations** (all optional, inert until configured): WhatsApp Cloud API, Petpooja POS,
  Borzo, Porter and Shiprocket couriers, Twilio, Telegram. See `.env.example`.

## Deploy

A live example runs at https://tulsifoods.app. Questions: rahullath02@gmail.com.

On Vercel the app boots into a deployment guide at `/` (the same page lives at `/guide` everywhere).
Vercel's disk resets, so use it to preview only; take orders from the Docker image below.

The `Dockerfile` runs the app. Mount a volume at `/data` for the database. Set `ADMIN_TOKEN`
and `BIZ_SITE_URL` in the environment.

## Direction

This is the shared base. Country-specific packs (payments, tax, couriers, compliance)
are planned as separate layers, with a focus on transparency for customers and for the
delivery partners who work with a kitchen.

See [CONTRIBUTING.md](CONTRIBUTING.md). Report security problems as described in
[SECURITY.md](SECURITY.md).
