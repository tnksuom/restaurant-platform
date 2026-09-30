# Deploying to Render

Two ways to put the app online, both in Render's Frankfurt (EU) data centre and both set up entirely in
the browser:

- **[Free demo](#free-demo)**: costs nothing. For showing the app to restaurant owners. It resets itself
  to the example restaurant every time it starts.
- **[Real restaurant](#real-restaurant)**: about $27 a month. Keeps data, photos and daily backups.

In both, **every change merged into `main` goes live automatically once GitHub's tests pass.**

## Free demo

What you get: the full app with the example restaurant "Bistro Demo" (a week of shifts, checklists,
stock, issues with a photo, a menu with photos and promotions) at an address like
`https://restaurant-ops-demo.onrender.com`.

Good to know before presenting:

- **It sleeps.** After 15 minutes without visitors it goes to sleep, and the next visit takes about a
  minute (Render shows a loading page). **Open the link a minute before you present.**
- **It resets.** Every start (after sleeping, and after each update) wipes everything and loads the
  example restaurant again. What you or the audience change during a presentation doesn't last,
  so every presentation starts clean.
- **Anyone with the link can sign in.** The sign-in page lists the demo accounts and their shared
  password, so people can try it on their own phones. Only share the link with people you want to try it.
- **The free database expires after 30 days.** See [renewing the demo](#renewing-the-free-demo).

### Set it up (about 10 minutes)

1. Go to <https://render.com> and sign up. Signing up **with GitHub** is easiest. No payment card is
   needed for free services.
2. In the Render dashboard, click **New +** → **Blueprint**.
3. Connect GitHub if asked, and give Render access to the **restaurant-ops** repository.
4. Choose **tnksuom/restaurant-ops**, branch **main**, and set **Blueprint Path** to
   `render.demo.yaml`. (Without this, Render uses `render.yaml`, which is the paid setup.)
5. Render lists what it will create: `restaurant-demo-db` and `restaurant-ops-demo`, both **Free**.
   Click **Apply**.
6. Wait for **Deploy live** in the `restaurant-ops-demo` service's **Events** tab (5–10 minutes the
   first time). Its address is shown at the top of that page.

### Presenting

- **Public website:** open the address. Customers' view: home, menu, contact.
- **Staff app:** tap **Try the staff app** in the yellow bar (or go to `/login/`), then tap a person:
  - **Olli Owner** or **Mia Virtanen (manager):** dashboard, staff, menu editor, promotions, timesheets
  - **Linh Nguyen (employee):** clock in, checklists on a phone, stock
  - **Eero Korhonen:** an employee who is on a break right now
- The shared password is shown on the sign-in page (`demo-bistro-2026` unless you change
  `DEMO_PASSWORD` under the service's **Environment**).

### Renewing the free demo

Render deletes a free database 30 days after it was created (it emails you before). To keep the demo:

1. Render → `restaurant-demo-db` → **Settings** → **Delete Database**.
2. Render → **Blueprints** → your demo blueprint → **Manual Sync**. This creates a new free database;
   the demo restarts on it and fills itself with the example restaurant.

### Removing the demo

Delete the blueprint's two services in Render (the database first, then the web service).

## Real restaurant

For an owner who wants to use it for real. Unlike the demo, nothing is reset: data and photos are kept
and backed up. Use `render.yaml` (the default Blueprint Path).

### What you will pay

Prices as listed on render.com in September 2026; Render shows the exact total before you confirm.

| Part | Render plan | Monthly |
|---|---|---|
| App server | `0.5c-512mb` (0.5 CPU, 512 MB) | $7 |
| Photo storage (issue photos, dish photos, logo) | 1 GB persistent disk, snapshotted daily | $0.25 |
| Database | Postgres `0.5c-1g` (1 GB RAM), daily backups, 7-day point-in-time recovery | $19 + storage |
| **Total** | | **about $27** |

The free plans are not suitable: free servers go to sleep after 15 minutes without visitors and cannot
keep uploaded photos, and the free database expires.

### 1. Create a Render account

1. Go to <https://render.com> and sign up. Signing up **with GitHub** is easiest.
2. Add a payment card under **Billing** (the Hobby workspace itself is free; you pay only for the parts above).

### 2. Create everything from the Blueprint

The file `render.yaml` in this repository describes the database, the app server and its settings.

1. In the Render dashboard, click **New +** → **Blueprint**.
2. Connect GitHub if asked, and give Render access to the **restaurant-ops** repository
   ("Only select repositories" is fine).
3. Choose **tnksuom/restaurant-ops**, branch **main**.
4. Render shows what it will create: `restaurant-db` (database) and `restaurant-ops` (web service).
   It then asks for four values. Fill them in:

   | Setting | What to enter |
   |---|---|
   | `INITIAL_RESTAURANT_NAME` | The restaurant's name, as customers should see it |
   | `INITIAL_OWNER_NAME` | Your name |
   | `INITIAL_OWNER_LOGIN` | The login you want, e.g. your email address |
   | `INITIAL_OWNER_PASSWORD` | A strong password: at least 10 characters, not a common word |

5. Click **Apply**. The first deploy takes about 5–10 minutes.

While it deploys, Render builds the app, creates the database tables and creates your owner account.
The **Events** tab of the `restaurant-ops` service shows progress; it ends with **Deploy live**.

### 3. Sign in and tidy up

1. Open the service's address (shown at the top of the `restaurant-ops` page, ending in `.onrender.com`).
   You see the public website.
2. Go to `/login/` on that address, for example `https://restaurant-ops.onrender.com/login/`,
   and sign in with the login and password from step 2.
3. **Remove the password from Render**: open the `restaurant-ops` service → **Environment**, delete
   `INITIAL_OWNER_PASSWORD`, and save. It was only needed once; your account keeps working.
4. Add your staff: **Menu → Staff → Add staff member**. Each person gets a one-time link to set their
   own password.

### Everyday running

- **Updates:** merge a pull request into `main`. GitHub runs the tests; when they pass, Render deploys
  the new version by itself. The app is unavailable for roughly half a minute during each deploy
  (services with a disk restart in place).
- **Is it working?** Render checks `/healthz` constantly and alerts you by email if the app stops
  answering. The same page must say `ok` in a browser.
- **Backups:** the database has point-in-time recovery for the last 7 days (Render → `restaurant-db`
  → **Recovery**). The photo disk is snapshotted every 24 hours and snapshots are kept for at least 7
  days (Render → `restaurant-ops` → **Disks**).
- **Logs:** Render → `restaurant-ops` → **Logs**. Passwords are never written to the logs.

### Using your own domain (optional)

1. Render → `restaurant-ops` → **Settings** → **Custom Domains** → add e.g. `www.yourrestaurant.fi`.
2. Render shows a DNS record to add at your domain registrar. Once it checks out, Render issues the
   HTTPS certificate by itself.
3. Render → `restaurant-ops` → **Environment**: add `DJANGO_ALLOWED_HOSTS` with the value
   `www.yourrestaurant.fi` (several domains are separated by commas) and save. The app redeploys and
   then accepts the new address. The `.onrender.com` address keeps working too.

### If something goes wrong

| What you see | What to do |
|---|---|
| Deploy fails at "pre-deploy" and the log says `INITIAL_OWNER_PASSWORD is not strong enough` | Choose a longer password under **Environment**, then **Manual Deploy → Deploy latest commit**. |
| Log says `No owner yet. Set INITIAL_OWNER_LOGIN and INITIAL_OWNER_PASSWORD` | Add both under **Environment**, then deploy again. |
| "Bad Request (400)" on your own domain | Add it to `DJANGO_ALLOWED_HOSTS` (see above). |
| A merged change is not live | The GitHub tests must pass first; check the repository's **Actions** tab. |
| Forgot the owner password | Another owner can create a reset link on your staff page. If you are the only owner, ask for help; the fix needs the Render shell. |

## Moving to another host later

The app is a standard Docker image (`Dockerfile`), so it also runs on Fly.io, Railway, a VPS or
anything else that runs containers. It needs PostgreSQL 14 or newer, a persistent folder mounted at
`/var/data`, and the environment variables listed in `render.yaml`, with
`python manage.py migrate --noinput && python manage.py ensure_owner` run before each new version starts.
