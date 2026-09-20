# RehoBothTek — Gadgets & Repairs website (Django)

"Something Different" — phones, laptops, accessories, and repairs, with a
built-in admin panel so you can manage stock, repairs, reviews, and content
without touching any code.

> ⚠️ **Note on testing:** this was written and reviewed carefully, but the
> environment I build in has no internet access, so I can't install Django
> there to click through it myself. Follow the steps below on your own
> computer — if anything errors, paste me the error message and I'll fix it
> immediately.

## Setup (fresh install or updating)

```bash
cd bytebench
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py makemigrations
python manage.py migrate
python manage.py seed
python manage.py createsuperuser   # first time only
python manage.py runserver
```

Then visit **http://127.0.0.1:8000/** for the site and
**http://127.0.0.1:8000/admin/** to manage everything.

## What's new — Launch-quality pass (accessibility, error pages, speed)

An audit of the finished site turned up a batch of things that weren't so much
*broken* as *unfinished* — states only a sighted mouse user could perceive,
errors that never reached the page, and a few pages doing more database work
than they needed to. This pass fixes those. Nothing about what the shop sells,
or how a customer buys it, changed.

**Accessibility — the parts that were invisible unless you went looking.**

- **Quick View is a real dialog now.** It was a box that *looked* like one:
  nothing moved focus into it, and Escape did nothing. It's marked up as a
  dialog (`role="dialog"`, `aria-modal`), named by its own heading, closes on
  Escape, keeps Tab inside it while open, and hands focus back to the card that
  opened it.
- **The toast that reports every add-to-cart and every failure was silent.**
  It carried no live-region semantics at all, so a screen reader never
  mentioned it. It's now a `role="status"` / `aria-live="polite"` region that
  ships empty in the page — a live region only announces changes made *after*
  it exists — and errors are routed to the assertive branch.
- **The search box was labelled only by its placeholder**, which vanishes the
  moment anyone types, leaving the field unnamed. It has a real label now; the
  visual design is unchanged.
- **A closed FAQ answer was still in the page**, hidden by animating its height
  to zero — which leaves the text in the tab order and in the accessibility
  tree, so a closed question still read out its own answer. Each question now
  reports `aria-expanded` and `aria-controls`, and a closed answer is genuinely
  removed rather than clipped, while still animating open.
- **Several states were signalled by colour alone**, which says nothing to a
  screen reader and is unreliable for a colour-vision deficiency. The wishlist
  heart reports `aria-pressed`, the selected category filter is marked
  `aria-current`, and status dots that sit alone with no text beside them are
  marked decorative.
- **One hover shade failed contrast.** The filled blue button passed at rest
  and dropped below the 4.5:1 minimum under the pointer — the one moment it is
  actually being aimed at. The hover shade now clears 4.5:1, and the test
  resolves it through the palette token rather than pinning a hex, so the
  colours stay free to change as long as the result stays readable.

**Error pages.** A 404 or a 500 used to fall through to whatever Django
rendered. Both have real pages now, and neither leaks exception detail — the
tests check that against a deliberately distinctive message rather than
assuming. With `DEBUG=True`, Django's own traceback pages still appear, because
they're the only thing that makes a traceback readable while you're developing.

**Fewer queries on the pages that were doing avoidable work.** The product
rating helpers, the account order pages, and the admin order changelist were
each re-deriving the same per-row values in a query of their own. They now
derive them once per page. The regression tests measure each page at two
different data sizes and assert that the count doesn't *grow* with the number
of rows — a hard-coded number would break on any unrelated change and would
prove nothing about the thing being tested.

**Errors that were being swallowed, and three small correctness fixes on the
way to checkout.** Checkout and contact validation failures used to reload a
blank-looking form with no hint of what was wrong; they are visible on the page
now. The payment page renders Paystack's SDK and handler exactly once, and
hands the customer's email to Paystack as a correctly escaped JavaScript string
— HTML-escaping is not sufficient inside `<script>`. The Track page refuses a
phone number too short to be real proof of ownership, instead of accidentally
matching a large share of orders. (Payment *verification* itself was tightened
in the security pass below.)

**One caveat, stated plainly.** This project has no JavaScript runner and no
headless browser, so anything that exists only inside a `<script>` block or the
stylesheet can be checked no further than "the branch is still written". The
tests that do that say so in their own names rather than pretending to be
behavioural. Everything asserted about rendered HTML is the real thing.

The whole suite — this batch and everything before it — runs with:

```bash
python manage.py test
```

It uses an isolated in-memory database, so your `db.sqlite3`, your products and
your orders are never touched.

## What's new — Production readiness

This pass was about the gap between "works on my machine" and "safe to put on
a real domain". Nothing about how the site behaves for a customer changed.

**Configuration can no longer fail silently.** With `DEBUG=False` the site
now refuses to start on a missing `SECRET_KEY` or a wildcard `ALLOWED_HOSTS`,
and warns loudly about anything still on a development value (console email,
`.local` sender address, Paystack test keys). See *What production checks when
it starts* below. Local development is untouched — none of it runs while
`DEBUG=True`.

**A real destination for errors.** `LOGGING` is configured to send
application logs to stderr, so the ERROR lines that `notifications.py` and
`paystack.py` already emitted — an email that failed to send, Paystack being
unreachable mid-verification — are no longer discarded.

**Django is pinned.** `requirements.txt` said `Django>=4.2,<5.1`, which
resolved to something different on every install and didn't match the version
this is actually built against. It's now `Django==5.2.17`.

**Pages describe themselves properly.** Every page has a canonical URL, and
Open Graph / Twitter metadata that product pages fill in from the product's
own name, spec, price and photo. `<title>` and `<meta name="description">`
are now set per page rather than shared site-wide, and there's JSON-LD for
the business (`LocalBusiness`, from your settings only) and for each product
(name, price, currency, availability — **no invented ratings or brands**).

**Media serving is documented, not guessed at.** WhiteNoise serves static
files but not uploaded product photos; that requirement, and the two ways to
meet it, are now written down in *Serving uploaded product photos in
production* below. No host-specific config was invented.

## What's new — Security, Payments & Inventory Hardening

This update focused entirely on making the store **secure and reliable**,
not on new features — per request, everything below was prioritized ahead
of anything else. Four things changed:

### 1. Order and payment pages can no longer be guessed

Previously, `/order/confirmation/5/` was viewable by anyone who changed the
number in the URL — meaning any customer could potentially browse other
customers' names, addresses, and order contents just by incrementing an
ID. Every order and repair booking now has a random, unguessable
**access token** (a UUID) that must be present in the URL alongside the ID:

```
/order/7/8f14e45f-ceea-467e-a1a7-7c3d3e9f6b2a/confirmation/
```

The confirmation page a customer lands on after checkout already includes
the correct token — nothing changes for them. But someone trying random
order numbers gets a 404, not someone else's order. This applies to order
confirmation, the payment page, and payment verification. The **tracking
page** (`/track/`) is deliberately different — it's meant to work even if
someone's lost their link, so it uses a different proof of ownership
instead (order/ticket number **and** the phone number used, both required
together, submitted via a form rather than a guessable URL).

If a customer contacts you needing their link resent, you can find it on
the order or repair booking's detail page in `/admin/` (a "Customer
confirmation link" field).

### 2. Paystack verification is now much stricter

Before, a payment was marked "paid" if Paystack confirmed *a* successful
transaction for the right amount. The gap: nothing checked that the
transaction being verified was actually **the one generated for that
specific order** — so, in theory, someone could take a genuine successful
payment reference from an unrelated past transaction of theirs (for the
same amount) and reuse it against a new, unpaid order. Verification now
requires all of:

- The reference matches exactly what was generated for this order at checkout
- Transaction status is `success`
- Amount matches the order total exactly, in kobo
- Currency is `NGN`
- The paying customer's email matches the order's email

Any mismatch on any of these fails the payment — it does not get marked paid.

### 3. Real inventory tracking (no more overselling)

Products now have a **stock quantity** (a real number, editable in
`/admin/`) instead of just a descriptive label. The "In stock / Low stock /
Out of stock" badge is now *calculated automatically* from that number —
you only ever edit the quantity, never the badge.

- Out-of-stock products show "Out of stock" instead of an Add to Cart button
- Adding to cart is capped at what's actually available
- Stock is re-checked again at checkout (in case it changed since you added it to your cart)
- Stock is decremented **atomically** at the database level — the same
  mechanism that makes bank transfers safe under concurrent access, so two
  customers genuinely can't both "win" the last unit, even if they check
  out in the same instant.

**One honest tradeoff, clearly documented rather than hidden:** for
pay-on-delivery orders, stock is reserved immediately when the order is
placed. For online payments, stock is reserved only once payment is
*verified* — not at checkout — so an abandoned payment attempt never
permanently ties up stock nobody actually paid for. The tradeoff is a very
narrow window where two customers could both reach the payment page for
the last unit of something. In the rare case this happens and someone pays
for stock that just sold out, the payment is *not* silently swallowed —
you get an urgent email flagging exactly which order and item needs manual
attention (refund, substitute, or restock). Building a full reservation
system with expiring holds would close this gap completely, but needs a
background job to expire abandoned holds — a reasonable next step, not
included here to avoid adding infrastructure complexity for what should be
a rare edge case.

### 4. Production security settings

`config/settings.py` now reads its sensitive configuration from
**environment variables** — `SECRET_KEY`, `ALLOWED_HOSTS`, both Paystack
keys, email credentials, and `DATABASE_URL` — falling back to safe
development defaults when they're not set, so nothing changes for local
development. See **`.env.example`** for the full list; copy it to `.env`
and fill in real values for local development, or set them as real
environment variables in your hosting platform for production (never
commit a real `.env` file — it's already in `.gitignore`).

When `DEBUG=False` (production), these switch on automatically:
HTTPS redirects, secure & HTTP-only cookies, HSTS, clickjacking protection,
and MIME-sniffing protection. They stay off in development because they'd
break `runserver` over plain `http://127.0.0.1:8000/`.

Also added: **PostgreSQL support** (set `DATABASE_URL` and it's used
automatically — otherwise SQLite, as before) and **WhiteNoise** for serving
static files in production without needing a separate web server like
nginx.

I simplified one part of this request: rather than splitting settings into
separate `dev.py`/`production.py` files, I used one `settings.py` whose
behavior changes based on the `DEBUG` environment variable. It achieves
the same practical outcome — genuinely different, safer behavior in
production — with fewer moving parts to keep in sync. Say the word if you'd
prefer the fully split version instead.

### Updating your local copy

This update touched models (new fields need a migration) and settings:

```bash
pip install -r requirements.txt
python manage.py makemigrations
python manage.py migrate
python manage.py seed
```

`requirements.txt` gained **python-dotenv**, **dj-database-url**,
**psycopg2-binary**, and **whitenoise** — that's what the `pip install`
step picks up.

## What's new — Phase 1 (homepage, product cards, trust signals)

- **Trust badges** below the hero: Genuine Products, Warranty Included,
  Secure Payments, Fast Delivery, Nationwide Shipping.
- **New homepage sections:** Featured Products, Best Sellers, New Arrivals,
  Featured Repair Services, Customer Testimonials, and an FAQ accordion.
  Best Sellers and Featured are toggles you control per-product in
  `/admin/` (tick "is bestseller" / "is featured") — not computed from
  fake numbers.
- **Product cards now show:** star ratings (from real approved reviews),
  discount badges (set a "compare at price" higher than the price in
  `/admin/` and the badge appears automatically), a wishlist heart, a
  "+ Add to compare" link, and a Quick View button that opens a modal
  without leaving the page.
- **Product pages now have:** a review system (customers leave reviews,
  you approve them in `/admin/` before they go public), related products,
  recently viewed products, a sticky "Add to cart" bar that appears once
  you scroll past the main button, wishlist/compare buttons, and a
  warranty/box-contents/delivery/returns info panel.
- **Live search suggestions** as you type on the shop page.
- **Wishlist and Compare** — both work without an account for now (stored
  in the browser session); they'll carry over cleanly once real customer
  accounts are added in a later phase.
- **A floating WhatsApp button** on every page.

### About the sample content — please read before launch

Two things in this update use **placeholder content you should replace**:

1. **Testimonials** (5) and **reviews** (8, spread across a few products)
   are clearly marked "Sample review — replace with a real one" in both
   the text and in `/admin/`. I wrote these to be realistic so you can see
   how the layout looks with content in it, but I won't pretend they're
   real customers — please swap them for genuine testimonials and reviews
   before the site goes live. Delete the sample ones in `/admin/` →
   Testimonials / Reviews once you have real ones.
2. **Best Sellers and Featured** are currently marked on products I picked
   for variety, not based on real sales (you have none yet). Adjust the
   checkboxes in `/admin/` → Products whenever you like.

I did **not** add fake "X people are viewing this" or "recently
purchased" counters — those are a common dark pattern that fabricates
urgency, and I won't build fabricated versions. Real-data equivalents are
a reasonable later addition once there's real order history.

## Updating from a previous version

Pull in `shop/`, `templates/`, `static/`, and `config/`, then run the setup
commands above again. Your existing database, products, and orders are
untouched — this only adds new tables/fields and loads new starter content
alongside what you already have.

## What's here

```
bytebench/
├── manage.py
├── config/                        ← settings, URL routing
├── shop/
│   ├── models.py                   ← Product, Review, Testimonial, FAQ, Order, etc.
│   ├── views.py                     ← page logic, including wishlist/compare
│   ├── admin.py                      ← what you see in /admin/
│   ├── session_lists.py               ← wishlist & compare (no login needed)
│   ├── cart.py                         ← shopping cart
│   ├── notifications.py                 ← automatic order/repair emails
│   ├── paystack.py                        ← online payment verification
│   ├── tests_*.py                       ← the test suite — see the notes above
│   └── fixtures/initial_data.json          ← starter products, reviews, FAQs etc.
├── templates/                       ← all pages
├── static/css/style.css               ← the site's design
├── static/images/                       ← your logo
├── media/products/                        ← product photos
└── requirements.txt
```

## Key settings to know about (`config/settings.py`)

All of these can be set as environment variables (see `.env.example`) —
the values below are just the development fallback defaults.

| Setting | What it's for |
|---|---|
| `SECRET_KEY` | Django's cryptographic signing key — must be a real secret in production |
| `DEBUG` | `True` locally, `False` in production (also toggles the security settings below) |
| `ALLOWED_HOSTS` | Your real domain(s) in production |
| `DATABASE_URL` | Set this to use PostgreSQL; otherwise SQLite is used |
| `SITE_URL` | Your public origin, e.g. `https://rehobothtek.com` — powers the canonical and Open Graph URLs. Unset locally |
| `WHATSAPP_NUMBER` | Where checkout/repair WhatsApp links go |
| `SITE_EMAIL` / `ADMIN_NOTIFY_EMAIL` | Where automatic order/repair emails land |
| `TELEGRAM_USERNAME` | Contact page Telegram link |
| `SHOP_ADDRESS` | Powers the embedded Google Map and directions link |
| `PAYSTACK_PUBLIC_KEY` / `PAYSTACK_SECRET_KEY` | Online payments — see below |
| `EMAIL_BACKEND` and `EMAIL_HOST_*` | Currently prints emails to your terminal — switch to real SMTP when ready |
| `EMAIL_USE_SSL` / `EMAIL_TIMEOUT` | Implicit TLS (port 465) instead of STARTTLS, and how long to wait on the mail server |
| `LOG_LEVEL` / `LOG_DIR` | Log verbosity, and an optional directory for a rotating log file |

### Turning on real email

By default, emails print to your terminal instead of sending. Easiest fix,
Gmail SMTP — set these as environment variables (or in your local `.env`):

1. Turn on 2-Step Verification on the sending Google account, then create
   an **App Password** at https://myaccount.google.com/apppasswords
2. Set:
   ```
   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
   EMAIL_HOST=smtp.gmail.com
   EMAIL_PORT=587
   EMAIL_USE_TLS=True
   EMAIL_HOST_USER=youraddress@gmail.com
   EMAIL_HOST_PASSWORD=the 16-character app password
   ```

### Setting up Paystack (online payments)

1. Create a free account at https://dashboard.paystack.com
2. **Settings → API Keys & Webhooks** → set `PAYSTACK_PUBLIC_KEY` /
   `PAYSTACK_SECRET_KEY` (as environment variables, or in `.env` locally)
   to your **Test** keys first.
3. Place a test order using a published test card (e.g. `4084 0840 8408
   4081`, any future expiry, CVV `408` — full list at
   https://paystack.com/docs/payments/test-payments/).
4. Once it works, switch to your **Live** keys from the same page.

**Never commit real secret keys to a public repository** — `.env` is
already in `.gitignore` for exactly this reason.

### Deploying with PostgreSQL

Locally, nothing changes — SQLite is still used automatically. For a real
deployment: create a PostgreSQL database on your host (Render, Railway, and
similar all offer this), set `DATABASE_URL` to the connection string they
give you, then run `python manage.py migrate` against it. Also run `python
manage.py collectstatic` as a deployment step — WhiteNoise serves the
files it produces. (This only matters once `DEBUG=False`; locally, static
files are served directly without needing `collectstatic` at all.)

### Serving uploaded product photos in production (MEDIA_ROOT)

There are two kinds of files in this project, and production has to treat
them differently:

| | What it is | Where it lives | Served in production by |
|---|---|---|---|
| **Static files** | CSS, JS, the logo — files that ship with the code | `static/`, collected into `staticfiles/` | **WhiteNoise**, automatically. Just run `collectstatic`. |
| **Media files** | Product photos uploaded through `/admin/` | `media/` (`MEDIA_ROOT`) | **Not WhiteNoise** — you have to serve this yourself. |

WhiteNoise only serves `STATIC_ROOT`. It deliberately does **not** serve
`MEDIA_ROOT`, and Django only serves media itself while `DEBUG=True` (see the
bottom of `config/urls.py`). So a deploy that runs `collectstatic` and stops
there will show the CSS and logo correctly but every product photo broken.

Pick **one** of these, whichever suits your host:

1. **Serve `media/` from whatever sits in front of the app.** Point a
   location/alias on your web server or platform's static-file service at the
   `media/` directory on disk, mapped to `MEDIA_URL` (`/media/`). Smallest
   change; no new services.
2. **Move uploads to object storage** (S3, Cloudflare R2, Backblaze B2, …)
   and switch `STORAGES['default']` to the matching backend. This is what you
   want if you run more than one instance or your host's filesystem is
   ephemeral — otherwise uploaded photos vanish on the next deploy.

Either way `MEDIA_URL` and `MEDIA_ROOT` stay exactly as they are: they say
where the files *are*, not who serves them.

**No host-specific configuration is included, on purpose.** This project
doesn't assume nginx, Apache, Docker, or any particular platform, so there's
no provider config file here to be wrong. Tell me which host you're deploying
to and I'll write the exact configuration for it.

### What production checks when it starts

With `DEBUG=False`, `config/settings.py` checks its own configuration before
serving anything.

**It refuses to start** if either of these is still on its development value,
because both are security problems rather than inconveniences:

- `SECRET_KEY` unset, or still the placeholder committed in the source —
  anyone who can read the repo could forge session cookies and password-reset
  tokens with it.
- `ALLOWED_HOSTS` unset, or still `*` — which switches off Django's
  Host-header validation entirely.

The error names the variable and how to generate a value for it.

**It warns and keeps running** about configuration that leaves the site up and
safe but not doing what you expect — a warning rather than a refusal, so a
half-configured deploy can still sell things while you fix it:

- Notifications are still going to the console instead of a real mailbox, so
  nobody is told when an order arrives.
- `DEFAULT_FROM_EMAIL` is still on a `.local` address, which mail providers
  reject or spam-file.
- Paystack is still on `pk_test_`/`sk_test_` keys, so no real money moves.

Read these in your host's startup log. Locally none of it runs at all —
`DEBUG=True` keeps the zero-setup development experience exactly as it was.

### Logging

Application logs go to **stderr**, which is what hosting platforms capture
and show you — so the errors that previously went nowhere now have somewhere
to land. That matters most for two of them: `shop/notifications.py` logs when
a notification email fails to send, and `shop/paystack.py` logs when payment
verification can't reach Paystack. Without a configured destination those
were being emitted and dropped.

`LOG_LEVEL` (default `INFO`) sets verbosity; `django.request` stays at
`ERROR` regardless, so failed requests are never lost to a quiet log level.
Set `LOG_DIR` to a writable directory if you'd also like a rotating
`bytebench.log` kept on disk — it's opt-in rather than automatic, since a
hard-coded path is one more thing that can be unwritable on a given host.

Nothing in the logging configuration records request bodies, POST data,
settings, or credentials — log lines name an order or a payment reference,
never a password, key, or card detail.

## What's coming next

This pass covered the 🔴 high-priority security/reliability items only, on
purpose. Still ahead, roughly in the order they were requested:

- **Product experience:** multi-image gallery with zoom, structured specs
  (brand/RAM/storage/colour/screen size) powering real filters, live search
  across those attributes rather than just the name.
- **Checkout polish:** a clearer multi-step flow, delivery fees (built so
  they can vary by zone/location rather than being hardcoded), loading and
  error states throughout.
- **Reviews tied to real orders:** so "verified purchase" is automatic
  rather than a manual checkbox.
- **Mobile:** a full mobile pass across every flow, and a mobile-optimized
  compare page. (Keyboard and screen-reader support for Quick View, and the
  rest of the accessibility batch, are done — see the launch-quality notes
  above.)
- **Performance & SEO:** WebP images, `sitemap.xml`, and `robots.txt`. (Lazy
  loading on the product cards, fewer queries on ratings, meta descriptions,
  canonical URLs, Open Graph/Twitter tags and JSON-LD are all done — see the
  notes above. `sitemap.xml` and `robots.txt` are the pieces that need the
  live domain to say anything useful; the WebP work doesn't, it's an image
  pipeline.)
- **Real email delivery testing** once you've got SMTP credentials set up.
- **Customer accounts, order history, loyalty, referrals** — deliberately
  last, since they build on everything above.

