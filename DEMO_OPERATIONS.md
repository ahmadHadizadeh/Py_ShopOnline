# Py_ShopOnline — Isolated Sales Demo

This document applies only to a separate Demo installation. It does not authorize
reusing the Production database, service account, environment file, media,
session store, or payment credentials.

## Public host

- Hostname: `demo.codeamoz.com`
- DNS: an `A` record named `demo` pointing to the Demo server IP.
- TLS: a dedicated Nginx virtual host and certificate for the Demo hostname.
- The Production apex and `www` virtual hosts must remain unchanged.

## Required isolation

The Demo must run with all of the following:

- A dedicated Linux service account and application checkout.
- A dedicated virtual environment, systemd unit, Gunicorn socket/port, and log path.
- A dedicated PostgreSQL database and a non-superuser PostgreSQL role.
- Dedicated `SECRET_KEY` and `PAYMENT_CALLBACK_SECRET` values.
- A dedicated environment file readable only by root and the Demo service group.
- Separate `staticfiles/` and `media/` directories.
- An Nginx virtual host whose `server_name` is only `demo.codeamoz.com`.
- No symlinks or shared writable paths into the Production application or media.

The Demo settings deliberately reject the Production database name, database
role, and test database name. Do not weaken those checks to make deployment
easier.

## Environment contract

Use independent random secrets; never copy Production values.

Required settings:

- `DJANGO_DEBUG=0`
- `DEMO_MODE=1`
- `ADMIN_URL_PATH=management`
- `SECRET_KEY=<independent random secret>`
- `PAYMENT_CALLBACK_SECRET=<independent random secret>`
- `ALLOWED_HOSTS=demo.codeamoz.com`
- `CSRF_TRUSTED_ORIGINS=https://demo.codeamoz.com`
- `DB_ENGINE=postgresql`
- `DB_NAME=pyshop_demo_db`
- `DB_USER=pyshop_demo`
- `DB_PASSWORD=<unique database password>`
- `DB_HOST=127.0.0.1`
- `DB_PORT=5432`
- `DB_TEST_NAME=pyshop_demo_test_db`
- `PAYMENT_DEFAULT_GATEWAY=mock_gateway`
- `ZARINPAL_ENVIRONMENT=sandbox`
- `SMSIR_API_KEY=` (empty)

Do not configure any ZarinPal merchant ID, callback URL, live payment endpoint,
SMS key, SMTP credential, or other real external transaction credential.
The Demo configuration fails closed if SMS or ZarinPal credentials are added.

## Initial setup sequence

With the Demo environment loaded and the separate database selected:

1. Install the project's pinned dependencies into the Demo virtual environment.
2. Run `manage.py check --deploy` and resolve Demo-specific configuration issues.
3. Run `manage.py migrate`.
4. Run `manage.py createcachetable pyshop_cache` if the database cache table is absent.
5. Run `manage.py create_demo_manager` using `DEMO_ADMIN_USERNAME` and
   `DEMO_ADMIN_PASSWORD` supplied only through the protected environment file.
6. Run `manage.py seed_demo_data`.
7. Run `manage.py collectstatic --noinput`.
8. Start the Demo service and verify its local health before enabling the Nginx host.
9. Configure TLS only after DNS resolves to the intended server.

The manager account is staff but not superuser. Its fixed role can manage the
catalog, reviews, sample orders, storefront branding, and Demo shipping options.
It cannot administer users, view carts, edit payment records, or receive
superuser privileges.

## Buyer experience

- A visitor can start a Demo session with one POST action; no SMS is sent.
- Each browser session receives its own non-staff, non-superuser customer account.
- A clearly fictitious default address is created so the visitor can complete
  checkout without entering personal contact details.
- Visitors must still be warned not to submit real contact, address, or payment
  information.
- The order and payment flow uses the signed Mock Gateway only. No money is
  collected and no real order is fulfilled.
- Visitor-owned orders, payments, addresses, and reviews are excluded from the
  shared Demo manager's administrative lists and dashboard.
- Visitor session lifetime is 24 hours. The cleanup command removes old
  temporary visitor accounts; a scheduled reset should run the seed command.

## Sample data reset

`manage.py seed_demo_data` is intentionally destructive **inside the isolated
Demo database only**. It clears Demo customers, carts, addresses, orders,
payments, reviews, wishlist data, inventory movements, catalog uploads, and
sessions, then recreates the sample catalog and lifecycle orders.

A reset must never be pointed at Production. The command refuses to run unless
`DEMO_MODE=1` and `DEBUG=False`.

For a smaller cleanup without resetting the sample catalog, use:

`manage.py purge_demo_customers --retention-hours 48`

## Public discovery and abuse controls

- `robots.txt` disallows the entire Demo site and does not publish a sitemap.
- Dynamic responses send `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet`.
- Storefront pages omit Production canonical and Open Graph URL metadata.
- Configure Nginx rate limits for `/accounts/demo-access/` and
  `/management/login/`. Trust only Nginx-normalized client IP information.
- Use request body limits and safe static/media serving. Uploaded media must not
  be executable.
- Do not place the shared Demo manager password in the public storefront,
  source control, customer-facing documentation, or a public QR code.

## Operational boundary

A Demo outage or reset must not restart, migrate, reconfigure, or write to
`pyshop.service`, `/etc/pyshop/pyshop.env`, `pyshop_db`, or Production media.
Keep Demo deployment, rollback, backup, logs, and maintenance commands scoped
to the dedicated Demo resources.
