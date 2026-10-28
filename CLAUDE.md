# restaurant-platform

Multi-restaurant platform (Django 5.2 + PostgreSQL), grown from restaurant-ops v1.0 (EP01–EP08). Organizations (customers) have locations (`core.Restaurant`) and staff; the platform layer lives in `tenants/`. Main domain: landing, sign-up, `/platform/` admin. Workspaces: `<slug>.<domain>` with the public site at `/` and the staff app at `/app/`. See README.md.

## Working rules
- Tenant isolation: every query for operational data filters by the current location (`request.user.restaurant`) or organization (`request.tenant`). Never look records up by id alone. Staff of a location: `Employee.objects.working_at(location)`. Add an isolation test for every new page.
- Business rules live in each app's `services.py`; views only parse input and call services. Every permission check happens on the server.
- History tables are append-only (audit events, stock movements, clock events, time corrections) and are protected by DB triggers. Never add update/delete paths for them; add compensating rows instead.
- Quantities are `Decimal` (3 dp), temperatures `Decimal` (2 dp). Never use floats for stock.
- Store instants in UTC; business dates are the restaurant's local date (`core.timeutils`).
- Mark every UI string for translation (`{% translate %}` / `gettext`).
- UI languages are en/fi/vi. When UI text changes, run `makemessages -l fi -l vi`, translate the new entries in both `.po` files and run `compilemessages`; commit the `.mo` files (`core/test_i18n.py` checks this).
- Every behavior change comes with a test. Run `python manage.py test` (needs PostgreSQL; see `.env.example`).
- Run `python manage.py makemigrations --check` before committing model changes.
