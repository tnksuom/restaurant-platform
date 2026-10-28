# Deploying to Render

Two ways to put the app online, both in Render's Frankfurt (EU) data centre and both set up entirely in
the browser:

- **[Free demo](#free-demo)**: costs nothing. For showing the app to restaurant owners. It resets itself
  to the example restaurant every time it starts.
- **[Live platform](#live-platform)**: about $27 a month. Keeps data, photos and daily backups.

In both, **every change merged into `main` goes live automatically once GitHub's tests pass.**

## Free demo

What you get: the platform's front page with two example restaurants, at an address like
`https://restaurant-platform-demo.onrender.com`: "Bistro Demo" (two locations, a week of shifts,
checklists, stock, issues with a photo, a menu with photos and promotions) and "Phở Corner" (a small
restaurant on a free trial), plus the platform administration. The free address has no sub-domains, so
visitors choose a restaurant on the front page and stay in it until they press "Choose another workspace".

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
3. Connect GitHub if asked, and give Render access to the **restaurant-platform** repository.
4. Choose **tnksuom/restaurant-platform**, branch **main**, and set **Blueprint Path** to
   `render.demo.yaml`. (Without this, Render uses `render.yaml`, which is the paid setup.)
5. Render lists what it will create: `restaurant-demo-db` and `restaurant-platform-demo`, both **Free**.
   Click **Apply**.
6. Wait for **Deploy live** in the `restaurant-platform-demo` service's **Events** tab (5–10 minutes the
   first time). Its address is shown at the top of that page.

### Presenting

- **Front page:** open the address. It explains the product and the plans, and has
  **Try a demo restaurant** buttons.
- **A restaurant:** tap **Bistro Demo**, then tap a person:
  - **Olli Owner:** both locations (switch in the bar under the header), **Locations**, **Plan and modules**
  - **Mia Virtanen (manager):** dashboard, staff, menu editor, promotions, timesheets
  - **Hoa Pham:** manager of the second location (Lahti)
  - **Linh Nguyen (employee):** clock in, checklists on a phone, stock
  - **Eero Korhonen:** an employee who is on a break right now
- **Another restaurant:** on the sign-in page, press **Choose another workspace**, then pick **Phở Corner**
  (Minh is the owner; the trial banner counts down).
- **Platform administration:** front page → **Platform administration**, sign in as `admin`. You see
  every restaurant, their plan, status and usage, and can suspend, activate or extend a trial.
- The shared password is shown on the sign-in page (`demo-bistro-2026` unless you change
  `DEMO_PASSWORD` under the service's **Environment**).

### Renewing the free demo

Render deletes a free database 30 days after it was created (it emails you before). To keep the demo:

1. Render → `restaurant-demo-db` → **Settings** → **Delete Database**.
2. Render → **Blueprints** → your demo blueprint → **Manual Sync**. This creates a new free database;
   the demo restarts on it and fills itself with the example restaurant.

### Removing the demo

Delete the blueprint's two services in Render (the database first, then the web service).

## Live platform

For running the platform for paying restaurants. Unlike the demo, nothing is reset: data and photos are kept
and backed up. Use `render.yaml` (the default Blueprint Path). You need your own domain (for example
`ravintola-app.fi`), because each restaurant gets its own address under it (`bistro.ravintola-app.fi`).

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
2. Connect GitHub if asked, and give Render access to the **restaurant-platform** repository
   ("Only select repositories" is fine).
3. Choose **tnksuom/restaurant-platform**, branch **main**.
4. Render shows what it will create: `restaurant-db` (database) and `restaurant-platform` (web service).
   It then asks for these values. Fill them in:

   | Setting | What to enter |
   |---|---|
   | `PLATFORM_DOMAIN` | Your domain without `www`, e.g. `ravintola-app.fi` |
   | `PLATFORM_NAME` | The product name shown on the front page |
   | `PLATFORM_CONTACT_EMAIL` | Where restaurants reach you about billing |
   | `INITIAL_ADMIN_NAME` | Your name |
   | `INITIAL_ADMIN_LOGIN` | Your platform admin login, e.g. your email address |
   | `INITIAL_ADMIN_PASSWORD` | A strong password: at least 10 characters, not a common word |

5. Click **Apply**. The first deploy takes about 5–10 minutes. It ends with **Deploy live** in the
   **Events** tab.

### 3. Connect your domain and its sub-domains

1. Render → `restaurant-platform` → **Settings** → **Custom Domains**: add `ravintola-app.fi` **and**
   `*.ravintola-app.fi` (the star covers every restaurant's address).
2. Render shows the DNS records to add at your domain registrar, including the ones that prove you own
   the domain for the wildcard certificate. Once they check out, Render issues HTTPS certificates itself.
3. Open `https://ravintola-app.fi`: you see the platform's front page.

### 4. Sign in as platform admin and tidy up

1. Go to `https://ravintola-app.fi/platform/login/` and sign in with the login and password from step 2.
2. **Remove the password from Render**: `restaurant-platform` → **Environment**, delete
   `INITIAL_ADMIN_PASSWORD`, and save. It was only needed once; your account keeps working.
3. Restaurants now sign up themselves on the front page, or you create them under
   **Organizations → New organization**. When a restaurant has paid, open it and set its status to
   **Active**. Trials that ended are counted on the Organizations page.

### Everyday running

- **Updates:** merge a pull request into `main`. GitHub runs the tests; when they pass, Render deploys
  the new version by itself. The app is unavailable for roughly half a minute during each deploy
  (services with a disk restart in place).
- **Is it working?** Render checks `/healthz` constantly and alerts you by email if the app stops
  answering. The same page must say `ok` in a browser.
- **Backups:** the database has point-in-time recovery for the last 7 days (Render → `restaurant-db`
  → **Recovery**). The photo disk is snapshotted every 24 hours and snapshots are kept for at least 7
  days (Render → `restaurant-platform` → **Disks**).
- **Logs:** Render → `restaurant-platform` → **Logs**. Passwords are never written to the logs.

### If something goes wrong

| What you see | What to do |
|---|---|
| Deploy fails at "pre-deploy" and the log says `INITIAL_ADMIN_PASSWORD is not strong enough` | Choose a longer password under **Environment**, then **Manual Deploy → Deploy latest commit**. |
| Log says `No platform administrator yet. Set INITIAL_ADMIN_LOGIN and INITIAL_ADMIN_PASSWORD` | Add both under **Environment**, then deploy again. |
| A restaurant's address says "Workspace not found" | Check the spelling, or find the restaurant under **Organizations** to see its address. |
| "Bad Request (400)" on your domain | Check that `PLATFORM_DOMAIN` is exactly your domain and `TENANT_SUBDOMAINS` is `true`. |
| A merged change is not live | The GitHub tests must pass first; check the repository's **Actions** tab. |
| A restaurant owner forgot their password | Another owner of that restaurant can create a reset link on their staff page. Otherwise it needs the Render shell. |

## Moving to another host later

The app is a standard Docker image (`Dockerfile`), so it also runs on Fly.io, Railway, a VPS or
anything else that runs containers. It needs PostgreSQL 14 or newer, a persistent folder mounted at
`/var/data`, and the environment variables listed in `render.yaml`, with
`python manage.py migrate --noinput && python manage.py ensure_platform_admin` run before each new version starts.
Sub-domains need a wildcard DNS record (`*.your-domain`) and a wildcard certificate.
