# Progress — Project Progress Tracker PWA

A lightweight project progress tracker with auth, built with FastAPI + PostgreSQL. Installable as a PWA on iOS/Android.

## Features

- **Auth** — Email + password signup/login, session-based
- **Projects** with color coding, groups, start/due dates
- **Tasks & Subtasks** nested up to 6 levels deep
- **Auto-calculated progress** — subtask completion rolls up
- **Overdue detection** — red warnings for past-due items
- **Daily targets** — how much %/day to meet deadlines
- **Today view** — overdue + due today tasks at a glance
- **Search** — find tasks/projects instantly
- **Stats** — activity heatmap, streak tracking, completion totals
- **Archive** — stash completed projects
- **Data export** — download all data as JSON
- **PWA** — install on iOS/Android, offline support

## Deploy to Render

1. Push to GitHub
2. Render Dashboard → **New → Blueprint**
3. Connect repo → auto-creates web service + database
4. Done! SECRET_KEY is auto-generated.

## Run Locally

```bash
pip install -r requirements.txt
python main.py
```

Visit `http://localhost:8000` — uses SQLite locally.

## Install on iOS

Open URL in Safari → Share → Add to Home Screen
