# Restaurant Platform

A multi-restaurant platform for internal management and operations: time clock, daily checklists and food-safety temperatures, inventory, staff management and a manager dashboard, with an optional public website and menu. It is a mobile-first web app, offered to many restaurants from one installation.

It grew out of the single-restaurant app [restaurant-ops](https://github.com/tnksuom/restaurant-ops) (v1.0, EP01–EP08). Everything that app does still works, now inside each customer's own workspace.

## The platform in one picture

```
Platform (one installation, one database)
├── Platform admin            → /platform/ on the main domain: organizations, plans, status, trials
├── Landing page + sign-up    → the main domain: 14-day free trial, no card
└── Organization (a customer, e.g. "Bistro Demo")   → bistro-demo.<domain>
    ├── Plan (Starter / Pro / Business) and status (trial, active, suspended, cancelled)
    ├── Modules: time clock, checklists, inventory, menu, public website (on/off)
    ├── Locations (branches), each with its own checklists, stock, shifts, dashboard
    └── Staff: owner(s), managers and employees, each assigned to one or more locations
```

- **Data separation.** One shared PostgreSQL database. Every operational row already carried `restaurant_id` (a location); every location now belongs to an organization. Each request is tied to one organization by its host name (`<slug>.<domain>`) or, on hosts without wildcard sub-domains, by the workspace chosen on the "Staff sign-in" page. A signed-in person is only ever served inside their own organization; anything else signs them out. Login names are unique per organization, so "anna" can exist in many restaurants.
- **Locations.** Owners add locations up to their plan's limit and choose where each person works. Everyone switches between their locations from the bar under the header; every screen shows the current location's data.
- **Plans and limits.** Plans set the number of locations, active staff and whether the website and menu are included. Prices are placeholders (29 / 59 / 99 € a month) to change before launch. New sign-ups get a 14-day trial; when it ends, the workspace pauses (data kept) until the platform admin activates it. Card payments are not built in yet: the admin sets a workspace to *Active* after payment.
- **Modules.** Owners switch modules on and off for their organization; switched-off modules disappear from the navigation and their pages return 404, with data kept.
- **Navigation.** The staff app's **☰ Navigation** groups pages by area: Time tracking, Checklists, Inventory, Team, Food menu and website, Business (owners) and Account. Each person sees only the groups for their role and the switched-on modules. In the staff app the dish editor is called *Food menu*, so it isn't confused with the navigation; the public website keeps *Menu*. **Sign out** sits in the top bar on every staff page.

## Stack

There was no existing repository, so this is a new project. The stack was chosen with the product owner:

| Part | Choice | Why |
|---|---|---|
| Web framework | **Django 5.2** (Python 3.11+) | Built-in secure sessions, password hashing, CSRF, migrations and test runner; server-rendered pages suit simple phone use. |
| Database | **PostgreSQL 14+** (tested on 16) | Transactions, row locks, partial unique indexes, check constraints and triggers enforce the business rules in the database too. |
| Images | **Pillow** | Validates and re-encodes issue photos. |
| Front end | Django templates, one CSS file, ~50 lines of optional JavaScript | Works without JavaScript; no build step. |

Everything is stored in PostgreSQL. Uploaded photos go to a private directory on disk. Nothing authoritative lives in the browser.

### Out of scope

The app does **not** integrate with payroll, point of sale or purchasing.

**Addresses.** On the main domain: the landing page `/`, sign-up `/signup/`, the workspace finder `/find/` and platform administration `/platform/`. In a workspace (`<slug>.<domain>`): the public website at `/`, `/menu/`, `/contact/` (if switched on), sign-in at `/login/`, the staff app at `/app/`, the dashboard at `/dashboard/`, and owners' settings at `/org/locations/` and `/org/account/`.

## Setup from a clean machine

```bash
# 1. Database (any PostgreSQL 14+). Example for a local server:
createuser -P restaurant            # choose a password
createdb -O restaurant restaurant

# 2. App
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                # then edit DATABASE_URL and DJANGO_SECRET_KEY
python manage.py migrate

# 3. Platform administrator (restaurants then sign up themselves at /signup/)
INITIAL_ADMIN_LOGIN=admin INITIAL_ADMIN_PASSWORD='choose-a-long-password' python manage.py ensure_platform_admin

# 4. Run (development)
python manage.py runserver   # platform http://localhost:8000 · a workspace http://<slug>.localhost:8000
```

Browsers send `*.localhost` to your own computer, so sub-domains work locally with `PLATFORM_DOMAIN=localhost` and `TENANT_SUBDOMAINS=true` (the defaults in `.env.example`).

**Demo data instead of step 3.** On an *empty* development database (`DJANGO_DEBUG=true`), run
`python manage.py seed_demo --password 'Choose-A-Password'`. It creates:

| Workspace | Plan | Accounts |
|---|---|---|
| `bistro-demo` (Bistro Demo, 2 locations: Jyväskylä and Lahti) | Pro, active, website on | `owner`, `mia` (manager), `hoa` (manager, Lahti), `linh` (both locations), `eero`, `sara` |
| `pho-corner` (Phở Corner) | Starter, trial ending in 9 days | `minh` (owner), `an` |
| platform | — | `admin` at `/platform/login/` |

Bistro Demo keeps the full single-restaurant demo: stock, a week of checklists and shifts, open issues with a photo, a corrected shift, menu, promotions and website images. All accounts use the password you give.

**To put it online, follow [DEPLOY.md](DEPLOY.md)**: a free self-resetting demo (`render.demo.yaml`) or a real installation (`render.yaml`), both on Render in the EU, deploying automatically after the tests pass. In general:

For production, run behind HTTPS with a WSGI server (for example `gunicorn config.wsgi`). Set `DJANGO_DEBUG=false` and run `python manage.py collectstatic`. Serve `staticfiles/` as static files. **Never** expose `PRIVATE_UPLOAD_ROOT` or `PUBLIC_UPLOAD_ROOT` directly through the web server: issue photos are served only by a permission-checked view, and dish photos go through the app so that photos of draft dishes stay hidden.

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | yes | — | `postgres://user:pass@host:5432/db` |
| `DJANGO_SECRET_KEY` | yes (unless `DJANGO_DEBUG=true`) | — | Long random string |
| `DJANGO_DEBUG` | no | `false` | `true` only for local development |
| `DJANGO_ALLOWED_HOSTS` | no | `localhost,127.0.0.1` | Comma-separated host names |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | no | — | e.g. `https://ops.example.com` |
| `DJANGO_SECURE_COOKIES` | no | `true` when not debugging | Secure-only cookies (needs HTTPS) |
| `RESTAURANT_TIME_ZONE` | no | `Europe/Helsinki` | Time zone for a newly created restaurant |
| `PRIVATE_UPLOAD_ROOT` | no | `./private_uploads` | Issue photo storage, outside any web root |
| `ISSUE_PHOTO_MAX_BYTES` | no | `10485760` | Photo size limit (10 MB) |
| `PUBLIC_UPLOAD_ROOT` | no | `./public_uploads` | Dish photos, logo and hero image |
| `MENU_PHOTO_MAX_BYTES` | no | `10485760` | Dish photo size limit (10 MB); photos are scaled to at most 1600 px |
| `PASSWORD_SETUP_LINK_TIMEOUT` | no | `259200` | Lifetime of one-time password links, in seconds (3 days) |
| `LOG_LEVEL` | no | `INFO` | |
| `DJANGO_BEHIND_PROXY` | no | `false` | `true` when a hosting proxy ends HTTPS and forwards `X-Forwarded-Proto` |
| `DJANGO_SSL_REDIRECT` | no | on when not debugging | Redirect plain HTTP to HTTPS (`/healthz` is exempt) |
| `DJANGO_HSTS_SECONDS` | no | 30 days when not debugging | How long browsers must use HTTPS |
| `RENDER_EXTERNAL_HOSTNAME` | set by Render | — | Added to the allowed host names automatically |
| `DEMO_MODE` | no | `false` | **Demo only.** Every start wipes the database and reloads the example restaurant; the sign-in page lists demo accounts. Never set it for a real restaurant. |
| `DEMO_PASSWORD` | no | `demo-bistro-2026` | Shared password of the demo accounts in demo mode |
| `PLATFORM_DOMAIN` | yes in production | `localhost` | Main domain; workspaces live at `<slug>.<PLATFORM_DOMAIN>` |
| `TENANT_SUBDOMAINS` | no | `false` | `true` when `*.PLATFORM_DOMAIN` points to the app (wildcard DNS and certificate). With `false`, workspaces are chosen on `/find/` and kept in the session |
| `TENANT_DEFAULT_SLUG` | no | — | Serve this workspace when the host names none (a single-restaurant installation) |
| `PLATFORM_NAME` | no | `Restaurant Platform` | Product name on the landing page |
| `PLATFORM_CONTACT_EMAIL` | no | — | Shown to owners for billing questions |
| `TRIAL_DAYS` | no | `14` | Length of the free trial |
| `INITIAL_ADMIN_LOGIN`, `INITIAL_ADMIN_PASSWORD`, `INITIAL_ADMIN_NAME` | first deploy only | — | Used by `python manage.py ensure_platform_admin` to create the platform administrator once; remove the password afterwards |

## Tests

```bash
python manage.py test                      # 194 tests, about 40 seconds; needs the database user to have CREATEDB
python manage.py verify_stock_ledger       # checks cached balances against the movement ledger
```

The tests cover the spec's minimum verification list:

- **Permission matrix:** manager-only pages and actions return 403 to employees. Nobody can see another person's shift. Deactivated staff lose their session. The last owner is protected.
- **Inventory:** the walkthrough arithmetic, rejection of negative stock, and low-stock at equality. Two parallel connections cannot overspend, and a double submit posts only once.
- **Checklists:** duplicate runs are prevented, including on simultaneous start. Missing required answers block submission, and template edits don't change past runs. Failed and out-of-range results open issues, and photo access is checked.
- **Time:** duplicate and parallel clock-ins are prevented, break cycles and active-break blocking work, and overnight shifts and both daylight-saving changes give correct durations. Corrections need a reason and keep old values.
- **Smoke test:** every screen renders for owner, manager and employee.
- **Platform (`tenants/tests.py`):** the same login name in two organizations signs in to the right one; a session from one organization is signed out on another; staff, stock and public images of another organization return 404; location limits, switching and assignment; managers only manage their own location's staff; staff limits on new and reactivated staff; plan changes; ended trials and suspensions pause the workspace; switched-off modules disappear; sign-up on sub-domains and on a single domain; the workspace finder and password links on a single domain; platform admin sign-in, status, trial and plan changes; the append-only platform history; and migrating a database from the single-restaurant app.

- **Navigation (`core/test_navigation.py`):** Sign out in the top bar and working; the groups each role sees; switched-off modules hide their whole group; the toggle is called *Navigation* and the dish editor *Food menu*.

## Manual test path

This follows the spec's walkthrough.

1. Sign in as the owner. Go to **Staff → Add staff member** and create a Manager and an Employee. Each gets a one-time link; open it in a private window to set that person's password.
2. As the Employee, open **☰ Navigation**. There is no *Team* group, and `/staff/` returns *403 Forbidden*. **Sign out** is always in the top bar.
3. As the Manager, go to **Inventory → Add item** and create *Rice*, kg, opening 5, minimum 5. It shows **Low stock**.
4. As the Employee, open *Rice* and record:
   - Received 2
   - Used 1
   - Waste 0.5, reason *Expired*

   The stock is **5.5 kg** and no longer low. Try *Used 6*: "Not enough stock", and nothing is saved.
5. As the Manager, create a checklist under **Checklist templates → New checklist**:
   - a required *Done / not done* task
   - a required *Temperature* task with location *Walk-in fridge* and limits 0 … 5 °C
6. As the Employee, go to **Checklists → Start** and fill in both tasks.
   - Enter 7.5 °C: a warning appears and an issue opens.
   - Use **Report a problem**, add a description and attach a photo.
   - Finish with **Review and submit**.
7. As the Manager, open **Checklist review**: the run shows as submitted and the reading is flagged. Open **Issues**, view the photo, and mark the issue resolved with a note.
8. As the Employee, go to **Clock in/out** and clock in, start a break, end the break, and clock out. The last shift shows break and worked time.
9. As the Manager, go to **Timesheets**, open the shift and choose **Correct this shift**. Change a time and give a reason. As the Employee, the shift shows **Corrected** with the before/after values and the reason.
10. As the Manager, go to **Staff**, open the Employee and choose **Deactivate**. Their open session stops working and they cannot sign in. Their stock movements, checklists and timesheet remain in the history under their name.

## Story status

All 46 stories are implemented: the MVP release (EP01–EP07, 42 stories) and EP08 Restaurant Administration (4 stories).

| Story | Status | Notes |
|---|---|---|
| US-03.01 Employee login | Done | Case-insensitive login, generic error, logout by POST, login required on every page. A deactivated user's session is rejected on its next request, and their sessions are also deleted. |
| US-03.02 Create employee | Done | Managers can create Employee or Manager only. The login is unique in any letter case. A one-time password link is shown to the manager to hand over; no password is generated or emailed. The account cannot sign in until the password is set. |
| US-03.03 Manage employee details | Done | Name and contact number, audited. Records keep the same employee id. Self-edit scope: staff can view their profile and change their own password only. |
| US-03.04 Assign roles | Done | Takes effect on the next request of an already signed-in user. The last active owner cannot be demoted. |
| US-03.05 Disable employee | Done | Requires a confirmation page. Blocked while the person has an open shift; the page links to *Correct this shift* so a manager can close it with a recorded reason. Reactivation is available. |
| US-04.01 Create inventory item | Done | Opening movement created when the opening quantity is above 0. Duplicate names within a category are rejected. The unit is locked once stock has been recorded. Items are deactivated, never deleted. |
| US-04.02 Record received stock | Done | |
| US-04.03 Record stock usage | Done | Row lock on the item, so parallel requests cannot go below zero. |
| US-04.04 Record waste | Done | Reason required (Expired, Damaged, Spilled, or Other with a note). Shown separately from usage. |
| US-04.05 Low-stock warning | Done | `current ≤ minimum`, active items only. The count is on the list and on Today. |
| US-04.06 View stock history | Done | Filter by item, type, staff and date. Every row shows the resulting balance. Paginated. Employees see history on each item page. |
| US-05.01 Create checklist template | Done | Ordered tasks with an optional group, four answer types (pass/fail, done/not done, temperature, stock level enough/low), a required flag and optional limits. At least one task is required. Each save creates a new version. Many tasks can be pasted at once from Google Sheets or Excel (Group and Task columns). |
| US-05.02 Complete daily checklist | Done | Start or resume, grouped tasks with progress per group, one tap per answer (saves and moves to the next task), a quantity/note box on every task, name and time stored with each answer. "Low" stock needs the quantity left and opens an issue for a manager. Submission is blocked while required tasks are unanswered. |
| US-05.03 Record temperature | Done | °C reading with location. A warning appears only when the manager set limits; the app never judges readings without them. |
| US-05.04 Add checklist notes | Done | A note is required for Fail, Not done and N/A. Whitespace-only notes are rejected. |
| US-05.05 Attach photo to report | Done | JPEG, PNG or WebP, up to 10 MB. Photos are re-encoded, which strips EXIF, GPS and embedded content. They get random storage keys and are served only after a permission check. An invalid photo means nothing is saved. |
| US-05.06 Report an issue | Done | Linked to the exact run and task. Stays open after submission. The resolution records who, when and a required note. |
| US-05.07 Review daily checklist | Done | Per-date counts: Submitted, In progress, Not started, failed and warnings. Also shows readings and open issues. An empty day says "no checklists expected" and never shows 100%. |
| US-05.08 View checklist history | Done | Filter by date, checklist, status and staff. Runs show their own snapshot, so they stay readable after template or staff deactivation. |
| US-06.01 Clock in | Done | Uses server time. Protected by an employee row lock plus a partial unique index. |
| US-06.02 Start break | Done | Idempotent: a repeat returns the running break. |
| US-06.03 End break | Done | |
| US-06.04 Clock out | Done | Blocked during a break, with guidance to end the break first. |
| US-06.05 View my working hours | Done | Filter by local start date. Shifts show open/closed and corrected state. Other people's shifts return 404. |
| US-06.06 Review employee hours | Done | Per-employee totals of closed shifts. Open shifts are flagged and shown as provisional. |
| US-06.07 Correct time record | Done | Manager/owner only, reason required. Validates order, break containment, overlap and conflicts with other shifts. Before/after snapshots and original clock events are kept and visible to the employee. |
| US-07.01 View today's operation | Done | `/dashboard/` (managers and owners only): staff on shift, hours worked, checklist progress, open issues, low stock and flagged temperatures, with a date picker. Hours for closed shifts and provisional hours for open shifts are shown separately. |
| US-07.02 View checklist status | Done | Every run with answered and required-left counts, templates not started, and each unanswered task with its checklist. Tasks can't be assigned yet, so the person who started the run is shown as responsible. |
| US-07.03 View low-stock items | Done | Active items at or below minimum with quantity, unit and supplier, emptiest first; each links to the item page. |
| US-07.04 View reported issues | Done | Open issues from any date, with time, description, source task and who reported it; issues resolved on the selected date are listed separately. |
| US-07.05 View staff status | Done | Who is clocked in right now, whether they are on a break and since when, and provisional worked time. |
| US-01.01 View homepage | Done | Name, logo, hero image, tagline, introduction, open-now status, active promotions and a few dishes with photos. Navigation (Home, Menu, Contact) works on phone and desktop. |
| US-01.02 View restaurant information | Done | `/contact/`: address, phone, email and the weekly opening hours with today highlighted and "Open now · until 21:00" or "Closed now · opens …". Periods past midnight (e.g. 12:00–02:00) are handled. |
| US-01.03 View restaurant location | Done | "Open in maps" uses a universal Google Maps link, which opens the maps app on phones and the website on computers. |
| US-01.04 View promotions | Done | Only published promotions whose dates include today (restaurant local date) appear; expired ones disappear by themselves. Managers create and edit them at `/manage/promotions/`. |
| US-01.05 Mobile-friendly website | Done | Home, menu and contact fit a 390 px phone without sideways scrolling (checked in a browser); the menu's category list scrolls within itself. |
| US-02.01 View menu | Done | `/menu/`: published dishes by category with name, description, price in euros, allergens (the 14 EU allergens), diet labels (VE, V, G, M, L) and availability. |
| US-02.02 Create menu item | Done | Name, category and price are required; the price must be 0 or more with at most two decimals ("12,50" and "12.50" both work). Saved as draft or published. |
| US-02.03 Edit menu item | Done | Changes to name, category, description, price and allergens appear on the public menu straight away when published. Changes are audited. |
| US-02.04 Disable menu item | Done | "Mark unavailable" / "Mark available" on each dish. Each dish chooses whether it is then shown as "Not available today" or hidden. |
| US-02.05 Manage menu categories | Done | Add, rename, reorder (up/down) and delete when empty. Dishes appear under their category and can be reordered too. |
| US-02.06 Upload dish photo | Done | JPEG, PNG or WebP up to 10 MB, with a preview before saving. Replace or remove later; the old file is deleted. Photos are re-encoded (metadata stripped) and scaled down. Photos of draft dishes are not public. |
| US-08.01 Manage restaurant information | Done | Owner only: **Navigation → Food menu and website → Restaurant details** (`/manage/restaurant/`). Name, tagline, introduction, address, phone and email; they appear on the homepage, contact page and footer straight away. Address and city go together; phone and email are checked. Changes are audited field by field. |
| US-08.02 Manage opening hours | Done | Owner only: **Navigation → Food menu and website → Opening hours** (`/manage/hours/`). Up to two periods per weekday (e.g. lunch and dinner); an empty day shows "Closed". Periods past midnight are allowed, but not into the next day's opening. Overlaps and half-filled times are explained per day and nothing is saved. |
| US-08.03 Manage special opening hours | Done | Same page, **Add special hours**: a date that is closed all day or has one or two periods, with an optional note ("Christmas Eve"). It replaces the regular hours on that date in the "Open now" status, the contact page (next 60 days) and search-engine data. One entry per date; dates in the past are refused. |
| US-08.04 Manage restaurant images | Done | Owner only: **Navigation → Food menu and website → Website images** (`/manage/images/`). Logo (scaled to 512 px), homepage photo and a gallery of up to 8 photos shown on the homepage; upload, replace, reorder and remove. The homepage and gallery photos need a short description for screen readers. Photos are re-encoded like dish photos, and a replaced or removed file is deleted and no longer served. |

## Design decisions and assumptions

- **Login identity = staff record.** The `staff.Employee` model is Django's user model, so the authentication user id *is* the employee id that all records reference. Login names are stored lowercase and are unique across the whole installation, which is stricter than "within a restaurant".
- **Append-only history.** These tables can only be inserted into:
  - `core_auditevent`
  - `inventory_stockmovement`
  - `timeclock_shiftevent`
  - `timeclock_timecorrection`

  The model layer refuses updates and deletes, and PostgreSQL triggers enforce the same rule for anything that bypasses the app. A stock reversal is a new manager adjustment with a reason.
- **Stock balance.** `current_quantity` on the item is a cache that is updated in the same transaction as each movement, under a row lock. Every movement also stores `balance_after`, and `verify_stock_ledger` compares the cache with `SUM(signed_quantity)`. Quantities are `NUMERIC(15,3)`, never floats. Movements use server time; staff cannot backdate them.
- **Double submit.** Each stock form carries a one-time idempotency key, and a repeated key returns the original movement. JavaScript also disables the submit button after the first tap.
- **Temperature answers.** The spec's status set is PENDING/PASS/FAIL/NOT_APPLICABLE. For temperature tasks, PASS means *reading recorded* and is shown that way. Whether the reading is in range is stored separately and is empty when no limits are set, so the app never implies food safety or legal compliance.
- **Automatic issues.** A failed task or an out-of-range reading opens one issue per task. Re-saving the same answer links the existing open issue rather than creating a duplicate, and the original description is kept.
- **Checklist visibility for employees.** Employees can open any of *today's* in-progress runs so the whole team can fill them. After submission they see runs they started, answered or submitted.
- **Editing submitted checklists.** Not supported in this release: submitted runs are read-only for everyone. Managers follow up through issues instead. This avoids silent changes to submissions.
- **Time corrections.** The form works in whole seconds in the restaurant's time zone. Fields left unchanged keep their exact stored instant. Times that don't exist because of daylight saving (e.g. 03:30 on the last Sunday of March) are rejected. Ambiguous times at the autumn change resolve to the first occurrence. A closed shift cannot be reopened by a correction, which prevents accidental duplicate open shifts.
- **Date rules.** A business day is the local calendar date. A shift belongs to the local date it started, even if it ends after midnight; the UI states this rule. Hours are summed in exact seconds and rounded to two decimals only for display.
- **Languages.** The staff app and the public site are in English, Finnish and Vietnamese. Anyone can switch with the EN / FI / VI buttons in the top bar, including before signing in; the choice is kept in a cookie for a year on that device. Content that people type in (dish names, checklist tasks, promotions, the restaurant introduction) is shown as entered.
- **Updating translations.** After adding or changing UI text, run `python manage.py makemessages -l fi -l vi`, fill in the new entries in `locale/*/LC_MESSAGES/django.po`, then run `python manage.py compilemessages`. The compiled `.mo` files are committed, so the Docker image needs no gettext. `core/test_i18n.py` fails if any entry is untranslated or fuzzy, if placeholders differ from the English text, or if a `.mo` file is out of date.
- **Restaurant administration is owner-only.** The EP08 stories are written for the owner, so managers cannot change the public details, hours or images (they still manage the menu and promotions). Special hours for a date override that date's weekly hours; a late period from the previous evening still runs past midnight as usual.
- **`restaurant_id`** is on every operational table, and every query filters by the signed-in user's restaurant. There is no multi-location administration.

## Known limitations

- **Billing is manual.** There is no card payment yet: the platform admin sets a workspace to *Active* after payment and can extend trials. Stripe (or Paytrail/Checkout Finland) is the next step.
- **The public website shows the organization's first location.** Owners with several locations get one website for now; per-location pages are a next step.
- **Roles are per organization.** A manager is a manager at every location they work at. The current location is stored on the person, so two devices of the same person switch together.
- **Sub-domains need a wildcard domain.** On a host without one (like the free `*.onrender.com`), staff choose their workspace once on "Staff sign-in".

- Dish names, descriptions and other typed-in content are stored in one language; per-language versions of content are not built yet.

- There is no email, so password resets are manager-issued one-time links.
- The template editor shows every task as a full form, which gets long for big checklists (64 tasks in the demo). New tasks are best added with the paste box. Task order is a number field, not drag-and-drop.
- No offline mode: an action needs a connection, and a failed save shows an error.
- There are no exports, approvals, overtime or wage calculations (out of scope).
- Photo storage is the local filesystem. Moving to object storage later only needs `checklists/photos.py` changed.
- Deactivating a user deletes their sessions by scanning the session table. That is fine at restaurant scale; a cache-backed session store would need a different approach.

## Project layout

```
config/       settings (env-driven), workspace URLs (urls.py) and platform URLs (urls_platform.py)
tenants/      platform – Plan, Organization, append-only OrganizationEvent, host → organization middleware,
              sign-up, workspace finder, locations, plan and modules, platform admin, ensure_platform_admin
core/         Restaurant (a location of an organization), append-only AuditEvent, time helpers, role checks, base layout, Today page
staff/        EP03 – Employee (auth user, per organization), login, one-time password links, staff admin
inventory/    EP04 – items, immutable stock ledger, verify_stock_ledger command
checklists/   EP05 – versioned templates, snapshotted runs, responses, issues, private photos
timeclock/    EP06 – shifts, breaks, immutable clock events, corrections
dashboard/    EP07 – read-only management dashboard (no tables of its own)
menu/         EP02 – menu categories and dishes, menu editor
website/      EP01 – public pages, public profile, opening hours, promotions
```

Business rules live in each app's `services.py`. Views only parse input and call services, so every rule is enforced on the server and tested directly.
