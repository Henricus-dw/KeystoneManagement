# Keystone

A project-execution dashboard for Asimotech — so the directors can see what the
dev team is building, where things stand, and what's blocked, without anyone
having to ask "what did you work on today?"

Built on **FastAPI · Jinja2 · SQLAlchemy (SQLite) · vanilla JS/CSS**. No build
step, no node, no external services.

---

## Running it

```powershell
# from c:\Keystone
.\run.ps1
```

…or manually:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

Then open **http://127.0.0.1:8000**.

The database (`keystone.db`) is created and seeded with demo data on first run.
Delete that file to start fresh.

### Assignment emails

Task and project assignment emails are sent through Microsoft Graph using an
Entra ID application registration. Grant the app the Microsoft Graph
**Mail.Send** application permission, grant admin consent, and set these
environment variables on the host before starting Keystone:

```bash
export KEYSTONE_GRAPH_TENANT_ID="your-tenant-id"
export KEYSTONE_GRAPH_CLIENT_ID="your-client-id"
export KEYSTONE_GRAPH_CLIENT_SECRET="your-client-secret"
export KEYSTONE_GRAPH_SENDER="keystonenotifications@professional.za.com"
export KEYSTONE_HOURS_REPORT_RECIPIENT="hours@example.com"
export KEYSTONE_APP_BASE_URL="https://keystone.example.com"
```

The sender must be a mailbox the application is allowed to send as. Restart the
app after setting these values. Monthly Hours Tracker submissions use
`KEYSTONE_HOURS_REPORT_RECIPIENT` as the destination and CC the submitting team
member automatically. Until that variable is set, reports are temporarily sent
to `taskeen@professional.za.com`.

### Monthly hours workflow

Hours are validated against 8 hours for each Monday-Friday working day in the
selected month, excluding South African public holidays. The due date is the
first working day of the following month. Each of Henri, Darryl, Jean, and
Taskeen submits once for the month; after all four submissions are received,
Keystone sends one workbook with a separate worksheet per member. At 12:00
SAST on the due date, missing members receive a reminder from the notification
mailbox.

### Demo logins (password is `keystone` for everyone)

| Email                        | Role      |
| ---------------------------- | --------- |
| `jean@professional.za.com`   | **Admin** |
| `adele@professional.za.com`  | Manager   |
| `henri@professionkal.za.com` | Developer |
| `darryl@professional.aero`   | Developer |

Admins additionally get **Team Members** (create/list users).

---

## The login & grand entrance

- The login screen plays `static/vid/loginbg.mp4` (your 720p clip) blurred, so it
  reads cleanly and atmospherically at 1080p.
- On a correct sign-in the login card **falls away**, and the Keystone emblem
  animates in the centre of the video as a **white holographic logo** —
  pop-in, flicker, chromatic-aberration ghosts, a sweeping sheen and scanlines,
  with the wordmark resolving below it. This only happens on the login → app
  transition (it's the entrance, not a permanent element).
- It's JS-driven (`static/js/login.js`): the browser posts credentials, plays the
  entrance, then navigates to the dashboard. With JS disabled it falls back to a
  normal redirect.

## Design language

- **Chakra Petch** — all headers, titles, labels, badges, codes (the "mono accents").
- **Inter** — body copy.
- Palette pulled from your reference screenshot and the logo: deep navy, teal/cyan
  accents (`#1CC4D8` / `#3FE0C2`), amber for in-progress/at-risk (`#E0B23C`), red
  for blocked, with a faint holographic grid in the background.
- Everything lives in `static/css/keystone.css` (+ `login.css`).

## What's in the app

- **Dashboard** — overall completion ring, projects in flight, at-risk / waiting-on-client
  counts, team activity feed, recently completed tasks.
- **Projects** — list + detail with derived progress, health, team, brief, and a board snapshot.
- **Kanban** — drag-and-drop across Todo → In Progress → Blocked → Testing → Done,
  with live progress recalculation and quick "add task".
- **Team View** — each developer's current work, blockers, and what they finished today.
- **Daily Reports** — the stand-up replacement: Today / Tomorrow / Blocked, submitted
  by devs and shown as a team timeline.
- **Analytics** — tasks by status, throughput per developer, projects by status, and an
  auto-generated weekly summary per project.
- **Task detail** — description, notes, comments/discussion, and metadata.
- **Admin** — team-member management.

---

## Notes on the original plan (TASKMASTER.pdf)

Henri's brief is a solid skeleton — the standout idea is **separating project
*health* from task *progress*** ("a project can be 80% done and still At Risk"),
and Keystone implements exactly that: `health` is its own field, and there's a
small rules engine (`app/services.py:recompute_health`) that flags At Risk /
Blocked / Delayed from blockers + deadlines, which a manager can still override.

A few gaps and inconsistencies I closed while building:

1. **The login system had no way to log in.** The `Users` table listed
   `Id / Name / Email / Role` but **no password/credential field** — so the one
   feature that was explicitly requested couldn't work. Added a hashed password
   (`pbkdf2`, stdlib) and real session auth, plus an `Admin / Manager / Developer`
   role so the directors' view differs from a dev's.

2. **"Assigned developer(s)" was modelled as a single `AssignedUserId`.** The text
   says *developers* (plural) on both projects and tasks, but the schema only allowed
   one. Made both proper many-to-many relationships (`project_members`,
   `task_assignees`).

3. **`Progress %` was a stored field.** A hand-edited percentage drifts out of sync
   immediately. Keystone **derives** progress from task state (done ÷ total), so it's
   always honest. (If you ever want a manual override, it's a one-line addition.)

4. **Status naming was inconsistent** — the example said "In Development" while the
   enum said "Development"; same for project "Risk: Medium" vs the Health table. I
   standardised on one `ProjectStatus` enum and one `ProjectHealth` enum and kept them
   distinct (see above).

5. **No timestamps anywhere.** `Tasks`, `Comments`, `ProgressReports`, `Attachments`
   all needed `created_at` (and tasks an `updated_at`) for the activity feed, "completed
   today", and "recently completed" to mean anything. Added throughout.

6. **`Comments` had no body and no time** (`Id / TaskId / UserId / Comment`). Gave it a
   real `body` + `created_at`.

7. **The activity feed / timeline had no data source.** The mockup showed times with no
   date and nothing produced them. Added a first-class `activities` table that the app
   writes to on moves, completions, comments and project creation.

8. **`ProgressReports` used `Yesterday / Today / Blocked`.** Your own examples on the
   next page actually show **Today / Tomorrow / Blocked** (a forward-looking stand-up),
   which is the more useful framing — so that's what I built, and linked each report to
   an optional project.

9. **Priority was on the wishlist but trivially worth having now** — it's a single column
   and it drives the dashboard ordering and the critical-project flags, so I included it.

### Deliberately left as scaffolding (your "in the future" / brainstorm tiers)

To keep scope tight and the demo crisp, these are **modelled in the schema but not yet
surfaced in the UI** — easy next steps rather than half-built features:

- **File attachments** — `Attachment` model + an `static/uploads/` folder exist; the
  upload UI isn't wired yet.
- **Notifications** — the events that would trigger them (assignment, mention, blocked,
  overdue) are already recorded as activities; turning those into per-user notifications
  is the remaining bit.
- **Git integration** (latest commit / link commits to tasks) — left as an integration
  point; the comment in the seed data ("Fixed in commit af8392") hints at where it slots in.
- **Burndown chart** — analytics has the per-status/per-project data; this is the one
  chart not yet drawn.

If you want, I can pick any of these up next.

---

## Layout

```
main.py                  FastAPI app, session middleware, static mount, startup seed
app/
  config.py              paths, secret, names
  db.py                  engine / session / Base
  models.py              the data model (see notes above)
  security.py            pbkdf2 password hashing
  deps.py                current-user + auth guards
  services.py            activity logging + health rules
  seed.py                tables + demo data
  templating.py          Jinja env, filters (timeago, badges…)
  routers/ auth.py pages.py api.py
templates/               base + login + one per view, partials/ macros
static/ css/ js/ img/ vid/ font/
```
