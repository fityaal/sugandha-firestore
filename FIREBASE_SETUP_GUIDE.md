# Sugandha — Firebase / Firestore Edition — Setup Guide

## What changed from the MySQL version

| Before (MySQL)                          | Now (Firestore)                                   |
|------------------------------------------|----------------------------------------------------|
| `flask_mysqldb` + raw SQL in every route | `firebase-admin` SDK + `firebase_db.py` helpers    |
| `items`, `firms`, `purchases`, `sales` tables | Same names, now as Firestore **collections**  |
| Integer `AUTO_INCREMENT` ids             | Firestore auto-generated string doc ids            |
| `bill_counter` table                     | `counters` collection + atomic transaction helper  |
| `shop_settings` row (id=1)               | `shop_settings/main` document                      |
| SQL `GROUP BY` / `LIKE` in reports & dashboard | Data pulled once per request, filtered/aggregated in Python (see note below) |
| No `users` table in the SQL you had      | Auto-seeded on first run: `admin` / `sugandha123` — **change this immediately** |

Every route, template, and URL your staff already know still works the same way —
only the storage layer underneath changed. No template files were rewritten
except one line in `purchase/entry.html` that assumed numeric ids in embedded
JavaScript (Firestore ids are strings).

## 1. Create the Firebase project

1. Go to https://console.firebase.google.com → **Add project**.
2. Once created, go to **Build → Firestore Database → Create database**.
   Choose **Native mode**, and a region close to Nagpur (e.g. `asia-south1`).
3. Go to **Project settings (gear icon) → Service accounts → Generate new private key**.
   This downloads a JSON file — rename it `serviceAccountKey.json` and place
   it in the project root (next to `app.py`). It's already in `.gitignore`,
   so it won't accidentally get committed or shared.

## 2. Install dependencies

```bash
pip install -r requirements.txt
```

## 3. Migrate your existing MySQL data (one-time)

If you still have the MySQL database running with your existing data:

```bash
pip install mysqlclient   # only needed for this one-time script
set MYSQL_HOST=localhost
set MYSQL_USER=root
set MYSQL_PASSWORD=yourpassword
set MYSQL_DB=sugandha
python migrate_to_firestore.py
```

This copies `items`, `firms`, `purchases`, `sales`, `shop_settings`, and
`bill_counter` into Firestore, keeping the same ids so nothing gets
disconnected. It also copies a `users` table if you have one; if not,
the app will create a default `admin` / `sugandha123` login the first
time it starts — **change that password immediately** via
Settings → Change Password.

Starting fresh instead? Skip this step — `app.py` seeds sensible defaults
(shop settings placeholder + default admin login) automatically.

## 4. Run the app

```bash
python app.py
```

Same as before — opens on `http://localhost:5002`.

## 5. Deploy security rules (only matters once a mobile app exists)

`firestore.rules` in this repo denies all direct client reads/writes by
default. That's intentional and **doesn't affect this Flask app at all** —
the Admin SDK always bypasses Firestore rules. It only matters if/when a
companion mobile app talks to Firestore *directly* instead of through Flask.

Deploy it (optional, for now) with the Firebase CLI:
```bash
npm install -g firebase-tools
firebase login
firebase deploy --only firestore:rules
```

## Why Python-side filtering instead of Firestore queries everywhere?

The reports (`/purchase/report`, `/sales/report`) and the dashboard combine
free-text search + date ranges + multiple filters. Firestore can do this,
but it requires a composite index for nearly every filter combination you
might click through in the UI, and substring search (`LIKE '%text%'`) isn't
something Firestore does at all.

For a single-shop dataset (hundreds to low tens-of-thousands of records),
pulling the collection and filtering in Python is simpler, cheaper to
maintain, and fast enough. If the shop's transaction history grows large
enough that this becomes slow (you'd notice report pages taking seconds to
load), the fix is either:
- Add composite indexes and push specific filters (date range, firm, status)
  back into Firestore `.where()` queries, keeping only free-text search in
  Python, or
- Add a proper search layer (Algolia/Typesense) fed by a Firestore trigger.

Neither is needed yet — just flagging it so it's a known, deliberate
trade-off rather than a surprise later.

## Web + mobile: now implemented

`routes/api.py` (mounted at `/api/v1/...`) is a JSON API layer that reuses
the exact same helper functions the web routes use — `next_sequence`,
IMEI sold-flag flip, GST math — so bill numbering and stock can never
drift between the two clients. Auth is a separate bearer-token scheme
(`api_auth.py`) since a mobile app can't easily ride the web portal's
cookie session.

The companion Android app (Flutter) lives in a separate `sugandha_mobile/`
project and talks only to this API, never to Firestore directly — see its
own README for setup and build instructions.
