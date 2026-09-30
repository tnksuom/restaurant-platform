# Deploying to Render

This guide puts the app online at an address like `https://restaurant-ops.onrender.com`, in Render's
Frankfurt (EU) data centre. Everything is done in the browser; no command line is needed.

After this setup, **every change merged into `main` goes live automatically once GitHub's tests pass.**

## What you will pay

Prices as listed on render.com in September 2026; Render shows the exact total before you confirm.

| Part | Render plan | Monthly |
|---|---|---|
| App server | `0.5c-512mb` (0.5 CPU, 512 MB) | $7 |
| Photo storage (issue photos, dish photos, logo) | 1 GB persistent disk, snapshotted daily | $0.25 |
| Database | Postgres `0.5c-1g` (1 GB RAM), daily backups, 7-day point-in-time recovery | $19 + storage |
| **Total** | | **about $27** |

The free plans are not suitable: free servers go to sleep after 15 minutes without visitors and cannot
keep uploaded photos, and the free database expires.

## 1. Create a Render account

1. Go to <https://render.com> and sign up. Signing up **with GitHub** is easiest.
2. Add a payment card under **Billing** (the Hobby workspace itself is free; you pay only for the parts above).

## 2. Create everything from the Blueprint

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

## 3. Sign in and tidy up

1. Open the service's address (shown at the top of the `restaurant-ops` page, ending in `.onrender.com`).
   You see the public website.
2. Go to `/login/` on that address, for example `https://restaurant-ops.onrender.com/login/`,
   and sign in with the login and password from step 2.
3. **Remove the password from Render**: open the `restaurant-ops` service → **Environment**, delete
   `INITIAL_OWNER_PASSWORD`, and save. It was only needed once; your account keeps working.
4. Add your staff: **Menu → Staff → Add staff member**. Each person gets a one-time link to set their
   own password.

## Everyday running

- **Updates:** merge a pull request into `main`. GitHub runs the tests; when they pass, Render deploys
  the new version by itself. The app is unavailable for roughly half a minute during each deploy
  (services with a disk restart in place).
- **Is it working?** Render checks `/healthz` constantly and alerts you by email if the app stops
  answering. The same page must say `ok` in a browser.
- **Backups:** the database has point-in-time recovery for the last 7 days (Render → `restaurant-db`
  → **Recovery**). The photo disk is snapshotted every 24 hours and snapshots are kept for at least 7
  days (Render → `restaurant-ops` → **Disks**).
- **Logs:** Render → `restaurant-ops` → **Logs**. Passwords are never written to the logs.

## Using your own domain (optional)

1. Render → `restaurant-ops` → **Settings** → **Custom Domains** → add e.g. `www.yourrestaurant.fi`.
2. Render shows a DNS record to add at your domain registrar. Once it checks out, Render issues the
   HTTPS certificate by itself.
3. Render → `restaurant-ops` → **Environment**: add `DJANGO_ALLOWED_HOSTS` with the value
   `www.yourrestaurant.fi` (several domains are separated by commas) and save. The app redeploys and
   then accepts the new address. The `.onrender.com` address keeps working too.

## If something goes wrong

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
