# restaurant-ops

Django 5.2 + PostgreSQL app for restaurant operations (EP03–EP07 done). See README.md for setup, story status and design decisions.

## Working rules
- Business rules live in each app's `services.py`; views only parse input and call services. Every permission check happens on the server.
- History tables are append-only (audit events, stock movements, clock events, time corrections) and are protected by DB triggers. Never add update/delete paths for them; add compensating rows instead.
- Quantities are `Decimal` (3 dp), temperatures `Decimal` (2 dp). Never use floats for stock.
- Store instants in UTC; business dates are the restaurant's local date (`core.timeutils`).
- Mark every UI string for translation (`{% translate %}` / `gettext`).
- Every behavior change comes with a test. Run `python manage.py test` (needs PostgreSQL; see `.env.example`).
- Run `python manage.py makemigrations --check` before committing model changes.
