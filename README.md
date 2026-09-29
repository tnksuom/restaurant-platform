# Restaurant operations — EP03–EP06

Staff access, inventory, daily checklists and time recording for one restaurant location. It is a mobile-first web app for employees and managers.

Implements the *Restaurant operations: implementation specification for EP03–EP06* (v1.0, 26 Sep 2026).

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

The app does **not** integrate with payroll, point of sale, purchasing, the public website/menu (EP01–EP02) or restaurant administration (EP08). The EP07 management dashboard is at `/dashboard/` for managers and owners; the "Today" page stays simple navigation.

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

# 3. First owner (the only account created outside the app; there is no sign-up page)
python manage.py create_owner --login owner@example.com --name "Owner Name" --restaurant "Bistro Name"
#   prompts for the password twice. For automated setup:
#   OWNER_PASSWORD='…' python manage.py create_owner … --password-from-env

# 4. Run (development)
python manage.py runserver          # http://127.0.0.1:8000
```

**Demo data instead of step 3.** On an *empty* development database (`DJANGO_DEBUG=true`), run
`python manage.py seed_demo --password 'Choose-A-Password'`. It creates a demo restaurant with an owner (`owner`), a manager (`mia`), employees (`linh`, `eero`, `sara`), stock, a week of checklists and shifts, open issues with a photo, and a corrected shift. All accounts use the password you give. It refuses to run on a database that already has staff.

For production, run behind HTTPS with a WSGI server (for example `gunicorn config.wsgi`). Set `DJANGO_DEBUG=false` and run `python manage.py collectstatic`. Serve `staticfiles/` as static files. **Never** expose `PRIVATE_UPLOAD_ROOT` through the web server; photos are served only by the permission-checked view.

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
| `PASSWORD_SETUP_LINK_TIMEOUT` | no | `259200` | Lifetime of one-time password links, in seconds (3 days) |
| `LOG_LEVEL` | no | `INFO` | |

## Tests

```bash
python manage.py test                      # 81 tests, about 5 seconds; needs the database user to have CREATEDB
python manage.py verify_stock_ledger       # checks cached balances against the movement ledger
```

The tests cover the spec's minimum verification list:

- **Permission matrix:** manager-only pages and actions return 403 to employees. Nobody can see another person's shift. Deactivated staff lose their session. The last owner is protected.
- **Inventory:** the walkthrough arithmetic, rejection of negative stock, and low-stock at equality. Two parallel connections cannot overspend, and a double submit posts only once.
- **Checklists:** duplicate runs are prevented, including on simultaneous start. Missing required answers block submission, and template edits don't change past runs. Failed and out-of-range results open issues, and photo access is checked.
- **Time:** duplicate and parallel clock-ins are prevented, break cycles and active-break blocking work, and overnight shifts and both daylight-saving changes give correct durations. Corrections need a reason and keep old values.
- **Smoke test:** every screen renders for owner, manager and employee.

## Manual test path

This follows the spec's walkthrough.

1. Sign in as the owner. Go to **Staff → Add staff member** and create a Manager and an Employee. Each gets a one-time link; open it in a private window to set that person's password.
2. As the Employee, open **Menu**. There is no Staff entry, and `/staff/` returns *403 Forbidden*.
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

All 26 EP03–EP06 stories and the 5 EP07 stories are implemented.

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
| US-05.01 Create checklist template | Done | Ordered tasks, three answer types, required flag, optional limits. At least one task is required. Each save creates a new version. |
| US-05.02 Complete daily checklist | Done | Start or resume, progress bar, per-task save with name and time. Submission is blocked while required tasks are unanswered. |
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
- **Translations.** All interface text is marked for translation, and Finnish and Vietnamese are declared in settings. Translation files are not written yet. Data such as task names is stored as entered, in Unicode.
- **`restaurant_id`** is on every operational table, and every query filters by the signed-in user's restaurant. There is no multi-location administration.

## Known limitations

- There is no email, so password resets are manager-issued one-time links.
- The template editor shows three empty task rows at a time; save and edit again to add more. Task order is a number field, not drag-and-drop.
- No offline mode: an action needs a connection, and a failed save shows an error.
- There are no exports, approvals, overtime or wage calculations (out of scope).
- Photo storage is the local filesystem. Moving to object storage later only needs `checklists/photos.py` changed.
- Deactivating a user deletes their sessions by scanning the session table. That is fine at restaurant scale; a cache-backed session store would need a different approach.

## Project layout

```
config/       settings (env-driven), URLs
core/         Restaurant, append-only AuditEvent, time helpers, role checks, base layout, Today page
staff/        EP03 – Employee (auth user), login, one-time password links, staff admin, create_owner command
inventory/    EP04 – items, immutable stock ledger, verify_stock_ledger command
checklists/   EP05 – versioned templates, snapshotted runs, responses, issues, private photos
timeclock/    EP06 – shifts, breaks, immutable clock events, corrections
dashboard/    EP07 – read-only management dashboard (no tables of its own)
```

Business rules live in each app's `services.py`. Views only parse input and call services, so every rule is enforced on the server and tested directly.
