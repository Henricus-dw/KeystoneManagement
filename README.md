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

For production, expose the app through the HTTPS reverse proxy at
**https://keystone.professionalgroup.co.za**. Uvicorn should remain bound to
`127.0.0.1:8000` behind that proxy; do not expose Uvicorn directly to the
internet.

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
export KEYSTONE_HOURS_REPORT_RECIPIENT="benjamin@professional.za.com"
export KEYSTONE_APP_BASE_URL="https://keystone.professionalgroup.co.za"
```

The sender must be a mailbox the application is allowed to send as. Restart the
app after setting these values. Monthly Hours Tracker submissions use
`KEYSTONE_HOURS_REPORT_RECIPIENT` as the destination and CC the submitting team
members (Taskeen, Darryl, Jean, and Henri). Until that variable is set, reports
are sent to `benjamin@professional.za.com`.

### Monthly hours workflow

Required hours are calculated as 9 hours for each Monday-Friday working day in
the selected month, excluding South African public holidays. The tracker shows
the previous month until the 20th, then switches to the current month. The due
date is the first working day of the following month. After the first of Henri,
Darryl, Jean, or Taskeen submits, the consolidated workbook becomes available
for review; each subsequent submission adds that member's worksheet. Any team
staff member can review and edit the workbook, but Send remains disabled until all
four have submitted. Clicking Send emails the workbook to the configured
recipient and CCs all four members. At 12:00 SAST on the due date, missing
members receive a reminder from the notification mailbox.

### Backup monitoring

Backup monitoring is read-only and available to staff and explicitly permitted guests through
two **Infrastructure** links:

- **E-Learning / Moodle Backups** (`/backups/learning`) shows E-Learning and
  Moodle LMS. Each has independent **Server backups** and **Backblaze B2**
  sections. B2 starts as **Pending configuration**, not Successful or Failed.
- **Other App Backups** (`/backups/apps`) shows Insight and AsimoLog server backups.

The old `/backups` link redirects to `/backups/learning`. Each destination shows
current status, last completion and last success, processed/repository bytes,
storage target, next scheduled time, reported backup contents (files,
directories or databases), and independent repository integrity checks.
Expand each destination's history for its latest 20 runs and 20 checks; older
records remain in the database. Page status counts count destinations, not
platforms. All displayed times are **SAST (UTC+02:00)**. Use **Refresh statuses**
to retrieve a fresh snapshot without leaving the current page.

`GET /api/backups` requires a Keystone login and returns all four platforms.
Each platform's `destinations` array contains independent destination snapshots.
For compatibility, the platform-level status/history fields and top-level
`counts` still describe **server backups only**; they are not an overall
protection status. `destination_counts` counts all destination statuses,
including pending B2 destinations.

The existing Restic jobs and systemd timers are **not changed or run by
Keystone**. No SSH access, restore, delete, or backup execution is added. New
tables and schema updates are applied automatically on application startup,
including in existing databases. Existing runs and integrity checks are
preserved as **server** records. Existing server-stored backups are not
automatically inspected: the backup scripts must report actual results and
contents. No reports or successful demo backups are seeded: server backups
start as **Unknown** until their jobs are connected to the reporting API.
Missing file/directory information is shown as **Not reported**, not guessed.

#### 1. Configure reporting tokens

Set four distinct, randomly generated tokens (at least 32 ASCII characters) in
Keystone's service environment, then restart Keystone:

| Platform key | Keystone environment variable |
| --- | --- |
| `e-learning` | `KEYSTONE_BACKUP_TOKEN_E_LEARNING` |
| `insight` | `KEYSTONE_BACKUP_TOKEN_INSIGHT` |
| `moodle` | `KEYSTONE_BACKUP_TOKEN_MOODLE` |
| `asimolog` | `KEYSTONE_BACKUP_TOKEN_ASIMOLOG` |

Generate each token separately with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Deliver only the matching platform's token to its reporting job (including
separate tokens for E-Learning and Insight on the same host). Store tokens in
restricted service/environment files, not source control or log output.
Rotate a token by replacing it on Keystone and the reporting host, then
restarting the affected services. Reporting returns **503** for a missing,
short, non-ASCII or reused platform token, **401** for an invalid bearer token,
**404** for an unknown platform, **422** for invalid reports, and **409** for
conflicting reuse of a report ID.

Use the existing **HTTPS** reverse proxy, with certificate verification enabled.
The reporting API is bearer-authenticated, not session-authenticated; a
Keystone login alone cannot submit reports. Do not expose plain HTTP ingestion
outside localhost. The proxy should limit request bodies (for example to
64 KiB, allowing for the bounded contents list) and apply an appropriate reporting rate limit.

#### 2. Connect each existing backup job

Send JSON to `POST /api/backups/{platform}/reports`, authenticated with
`Authorization: Bearer <that-platform-token>`. For example, this is a
**payload shape**, not a statement of the production schedules or results:

```json
{
  "run_id": "2026-10-23T01:30:00Z",
  "destination_kind": "server",
  "status": "successful",
  "started_at": "2026-10-23T01:30:00Z",
  "completed_at": "2026-10-23T01:45:00Z",
  "next_scheduled_at": "2026-10-24T01:30:00Z",
  "overdue_at": "2026-10-24T03:30:00Z",
  "destination": "AsimoLog physical server",
  "contents": ["/srv/example-app", "/etc/example-app", "Database dump: /var/backups/example.sql"],
  "processed_bytes": 123456789,
  "repository_bytes": 987654321
}
```

- Set `destination_kind` to `server` for backups on another server or `b2`
  for Backblaze B2. Omitting it defaults to `server`, so existing scripts
  continue to work unchanged. B2 reporting is supported for E-Learning and
  Moodle; B2 reports for the other apps are rejected with **422**.
  Once B2 jobs are configured, send their reports to the same platform
  endpoint using that platform's token, with `destination_kind: "b2"` and a
  safe B2 target display name. No B2 credentials are stored in Keystone.
- Use a unique `run_id` per destination and invocation, reused for its running/final reports and
  network retries. IDs allow letters, digits, `_`, `.`, `:`, and `-` (120
  characters maximum). A Restic snapshot ID alone is unsuitable because failed
  runs may not produce a snapshot.
- Supply `contents` from the actual backup script inputs: a list of files,
  directories and/or database dump descriptions included in that run. It is
  stored with the run and shown in both current status and history. Use at
  most 100 nonblank entries of at most 500 characters each; surrounding
  whitespace is stripped. Do not include credentials, connection strings or
  file contents. Omit the list (or send `[]`) if unknown: the page says
  **Not reported**. Keystone does not scan storage or infer scope from old runs.
- At job start send `status: "running"`, omit `completed_at`, and supply
  `expected_completion_at`: the deadline after which this run is considered
  stalled. At completion send `successful` or `failed` with `completed_at`.
  A failed run requires a sanitized `error` (maximum 4,000 characters).
  Report nonzero Restic exits, including partial/incomplete backups, as failed.
- Timestamps must include an offset (`Z` or `+02:00`); Keystone normalizes them
  to UTC. Run start and completion cannot be more than five minutes in the
  future. Keep the hosts' clocks synchronized.
- Read `next_scheduled_at` from the **actual systemd timer**, for example using
  `systemctl show <existing-backup>.timer --property=NextElapseUSecRealtime --value`,
  and convert it to ISO 8601 with an offset. Configure `overdue_at` as that
  next run's expected completion/report deadline. Keystone does not assume
  daily schedules or use the illustrative screenshot's times.
- If the timer has no next run, fix/confirm the server's timer configuration
  before reporting: the API requires explicit next-run/deadline times rather
  than silently inventing a schedule. At initial rollout, report the last
  **known actual** result with its current next-run/deadline, or wait for the
  next real run; never invent a successful result.
- Set `destination` to a safe display name, not a credential-bearing Restic
  URL. For server backups, use AsimoLog physical server for E-Learning, Insight and Moodle, and
  E-Learning server for AsimoLog, after verifying the current configuration.
- Byte counts are optional nonnegative integers. `processed_bytes` is the
  logical data processed for that run (for example Restic summary
  `total_bytes_processed`). `repository_bytes` is actual repository storage
  usage measured on the destination, **not** logical snapshot size or newly
  added bytes. Omit unavailable metrics; the UI says **Not reported**, not zero.

Example submission from the reporting host, after constructing an actual
`backup-report.json` and configuring the restricted curl config described below:

```bash
curl --fail-with-body --silent --show-error \
  --connect-timeout 5 --max-time 15 \
  --config /etc/keystone-backup-curl.conf \
  --header "Content-Type: application/json" \
  --data-binary @backup-report.json \
  "https://keystone.professionalgroup.co.za/api/backups/e-learning/reports"
```

The restricted curl config (mode `0600`, owned by the reporting service user)
contains `header = "Authorization: Bearer <that-platform-token>"`. Do not put
tokens on the command line, enable shell tracing, or log request headers.
Do not send Restic passwords, SSH keys, database credentials, raw connection
strings or unredacted command output. Unknown JSON fields are rejected.

Preserve the original backup command's exit status **before reporting**. Handle
reporting/network failures separately: log a sanitized monitoring error and
queue the unchanged payload for retry, then exit with the **original backup
exit status**. In particular, handle failures explicitly under shell `set -e`;
failed reporting must not turn a successful Restic job into a failed job.
Retrying an identical report is idempotent. A run can advance from Running to
a final result but a final result is immutable; delayed reports cannot replace
a newer run in the dashboard. Do not let a failed start-report submission stop
the backup itself.

#### 3. Report integrity checks separately

Only after an existing `restic check` completes, send its actual result to
`POST /api/backups/{platform}/checks` with the same platform token:

```json
{
  "check_id": "check-2026-10-23T04:00:00Z",
  "destination_kind": "server",
  "status": "passed",
  "checked_at": "2026-10-23T04:20:00Z"
}
```

Use `status: "failed"` plus a sanitized `error` on failure. Set
`destination_kind: "b2"` for a B2 repository check; omitted means `server`.
Run IDs and check IDs are scoped by platform **and destination kind**, so the
same ID can be used independently for server and B2. A check ID is unique
and immutable within that scope; identical retries are accepted. The page displays
the latest check timestamp and result independently of the backup result,
without assuming check frequency or marking old checks as fresh.

**Status rules:** no run reports means Unknown, except B2 with no run or check
reports starts as Pending configuration. A B2 check alone changes it to Unknown,
not Successful: an integrity check is not evidence that a backup completed.
A reported run is Running,
Successful or Failed. A running job becomes Overdue strictly after its
`expected_completion_at`; a successful destination becomes Overdue strictly after
its `overdue_at` if no newer run has arrived. A failed run remains Failed
until a newer run arrives, so a missed window cannot conceal an explicit
failure. History keeps original results, not derived Overdue labels.
Missing reports are detectable only after the first valid run/schedule report.
Notifications and email alerts are not part of this version.

### Demo logins (password is `keystone` for everyone)

| Email                        | Role      |
| ---------------------------- | --------- |
| `jean@professional.za.com`   | **Admin** |
| `adele@professional.za.com`  | Manager   |
| `henri@professionkal.za.com` | Developer |
| `darryl@professional.aero`   | Developer |

Admins additionally get **Team Members** (create/list users).

### Guest role and page access

Admins can select **Guest** when creating or editing a member under
**Team Members**, then tick that person's **Guest page access** checkboxes.
Permissions are stored separately for each guest, not shared across the role.
New guests have **no page grants** by default. Saving with no boxes selected
revokes all optional page access. Changing a user away from Guest clears the
guest grants; converting them back requires selecting pages again.

- Guests have **read-only** access to selected pages. Navigation hides
  unselected pages, and direct URLs, downloads and JSON endpoints enforce the
  same permissions. Guests cannot create/edit/delete records, comment, upload,
  move tasks, submit hours, edit/send workbooks or manage members.
- Account settings (including changing their own password/avatar colour) and
  sign-out are always available. Login opens the first allowed page in menu
  order, or Account if no pages are selected.
- **Projects** includes project details/files and task details/files linked
  from those projects. **Kanban** includes task details/files. Either grant
  enables task detail access; neither implicitly grants the other main page.
- Each backup page has a separate grant. The backup JSON endpoint returns
  only platforms from the granted backup page(s).
- Analytics and Activity can be explicitly granted read-only, including the
  Activity PDF export. Team Members and release-management forms remain
  admin-only. Existing Admin, Manager and Developer access is unchanged.
- The **Servers** grant includes credentials on server entries the user can
  already view (shared entries or their own); it does not grant access to
  another user's private entries. Grant this page only to trusted guests.
- Hours Tracker includes read-only review of existing team workbooks. Guest
  page visits do not create or update workbook files.

Permission changes take effect on the guest's next request, without requiring
them to sign out. Restart Keystone after deploying this change: the existing
database gains a `guest_pages` column automatically, preserving staff roles
and starting with empty guest grants.

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
  counts, team activity feed, and all of the signed-in user's outstanding assigned tasks.
  Tasks with deadlines appear earliest first; tasks without deadlines follow, grouped by project.
- **Projects** — list + detail with derived progress, health, team, brief, and a board snapshot.
- **Kanban** — drag-and-drop across Todo → In Progress → Blocked → Testing → Done,
  with live progress recalculation and quick "add task".
- **Task assignment** — when adding a task from a project or the Kanban board,
  select one or more people from the entire team, including admins, managers,
  and people outside the project. You are selected by default; clear your
  selection to assign only to someone else. If nobody is selected, the task
  is assigned to you. Assignees appear on task cards and task details and receive
  assignment emails when Microsoft Graph is configured. Only the person who
  created/assigned a task can change its details or move it between statuses.
- **Team View** — each developer's current work, blockers, and what they finished today.
- **Daily Reports** — the stand-up replacement: Today / Tomorrow / Blocked, submitted
  by devs and shown as a team timeline.
- **Analytics** — tasks by status, throughput per developer, projects by status, and an
  auto-generated weekly summary per project.
- **Task detail** — description, notes, comments/discussion, and metadata.
- **Servers** — any signed-in user can edit or delete shared entries, regardless of
  creator. Private entries and sharing controls remain restricted to the owner/admin.
  Deleting an entry requires confirmation and removes its stored credentials and cover image,
  not the actual VM or server.
  Image uploads offer **Fit entire image** to avoid cropping; the saved choice applies
  to the server card, detail view, and edit preview. Existing images keep their fill
  style unless changed. You can save a new fit setting without re-uploading the image.
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
