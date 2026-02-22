from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, func as sqlfunc
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, timedelta
import os

from database import engine, get_db, Base
from models import User, Project, ProjectMember, Task, TaskAssignee, ActivityLog, Comment
from auth import create_user, authenticate_user, get_user_by_id

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Progress")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ──── Auth helpers ────

def get_current_user(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    return get_user_by_id(db, uid) if uid else None

def require_user(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401)
    return user


# ──── Access helpers ────

def user_projects(db: Session, user_id: int, archived: bool = False):
    """Get all projects a user is a member of."""
    return db.query(Project).join(ProjectMember).filter(
        ProjectMember.user_id == user_id, Project.is_archived == archived
    ).order_by(Project.sort_order, Project.id).all()

def get_user_project(db: Session, user_id: int, project_id: int):
    """Get project if user is a member. Returns (project, membership) or raises 404."""
    result = db.query(Project, ProjectMember).join(ProjectMember).filter(
        Project.id == project_id, ProjectMember.user_id == user_id
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Project not found")
    return result[0], result[1]

def get_user_task(db: Session, user_id: int, task_id: int):
    """Get task if user is a member of its project."""
    t = db.query(Task).join(Project).join(ProjectMember).filter(
        Task.id == task_id, ProjectMember.user_id == user_id
    ).first()
    if not t:
        raise HTTPException(status_code=404)
    return t

def get_project_members_map(db: Session, project_id: int):
    """Returns {user_id: {id, name, email, initials, role}}"""
    members = db.query(ProjectMember).options(joinedload(ProjectMember.user)).filter(
        ProjectMember.project_id == project_id
    ).all()
    return {m.user_id: {
        "id": m.user_id, "name": m.user.display_name, "email": m.user.email,
        "initials": m.user.initials, "role": m.role
    } for m in members}

def can_edit(membership: ProjectMember) -> bool:
    return membership.role in ("owner", "editor")


# ──── Schemas ────

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    color: str = "#6366f1"
    start_date: Optional[str] = None
    due_date: Optional[str] = None
    group_name: str = ""

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    start_date: Optional[str] = None
    due_date: Optional[str] = None
    is_archived: Optional[bool] = None
    group_name: Optional[str] = None

class TaskCreate(BaseModel):
    title: str
    notes: str = ""
    parent_task_id: Optional[int] = None
    due_date: Optional[str] = None
    assigned_to: Optional[List[int]] = None

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    progress: Optional[float] = None
    is_completed: Optional[bool] = None
    due_date: Optional[str] = None
    assigned_to: Optional[List[int]] = None

class MemberInvite(BaseModel):
    email: str
    role: str = "editor"

class MemberUpdate(BaseModel):
    role: str

class CommentCreate(BaseModel):
    text: str


# ──── Utilities ────

def parse_date(d: Optional[str]) -> Optional[date]:
    if d and d.strip():
        return date.fromisoformat(d)
    return None

def calc_project_progress(db: Session, project_id: int) -> float:
    top = db.query(Task).filter(Task.project_id == project_id, Task.parent_task_id.is_(None)).all()
    return sum(t.progress for t in top) / len(top) if top else 0.0

def recalc_task_progress(db: Session, task: Task):
    children = db.query(Task).filter(Task.parent_task_id == task.id).all()
    if children:
        task.progress = sum(c.progress for c in children) / len(children)
        task.is_completed = task.progress >= 100.0
    db.commit()
    if task.parent_task_id:
        parent = db.query(Task).get(task.parent_task_id)
        if parent:
            recalc_task_progress(db, parent)

def get_task_tree(db: Session, project_id: int, members_map: dict):
    all_tasks = db.query(Task).filter(Task.project_id == project_id).order_by(Task.sort_order, Task.id).all()
    task_ids = [t.id for t in all_tasks]

    # Get comment counts in one query
    comment_counts = dict(db.query(Comment.task_id, sqlfunc.count(Comment.id)).filter(
        Comment.task_id.in_(task_ids)
    ).group_by(Comment.task_id).all()) if task_ids else {}

    # Get all assignees in one query
    all_assignees = db.query(TaskAssignee).filter(
        TaskAssignee.task_id.in_(task_ids)
    ).all() if task_ids else []
    task_assignees_map = {}
    for ta in all_assignees:
        task_assignees_map.setdefault(ta.task_id, []).append(ta.user_id)

    task_map = {}
    for t in all_tasks:
        assignee_ids = task_assignees_map.get(t.id, [])
        assignees_info = [members_map[uid] for uid in assignee_ids if uid in members_map]
        task_map[t.id] = {
            "id": t.id, "title": t.title, "notes": t.notes,
            "progress": round(t.progress, 1), "is_completed": t.is_completed,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "parent_task_id": t.parent_task_id, "children": [],
            "assignee_ids": assignee_ids,
            "assignees": assignees_info,
            "comment_count": comment_counts.get(t.id, 0),
        }
    roots = []
    for t in all_tasks:
        node = task_map[t.id]
        if t.parent_task_id and t.parent_task_id in task_map:
            task_map[t.parent_task_id]["children"].append(node)
        else:
            roots.append(node)
    return roots

def get_days_info(project):
    today = date.today()
    total_days = days_left = None
    is_overdue = False
    if project.start_date and project.due_date:
        total_days = (project.due_date - project.start_date).days
    if project.due_date:
        days_left = (project.due_date - today).days
        is_overdue = days_left < 0
    return {"total_days": total_days, "days_left": days_left, "is_overdue": is_overdue}

def log_activity(db: Session, user_id: int, project_id: int, action: str, detail: str = ""):
    db.add(ActivityLog(user_id=user_id, project_id=project_id, action=action, detail=detail))
    db.commit()

# Accent colors for member avatars
MEMBER_COLORS = ["#6366f1", "#ec4899", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#f97316"]


# ──── Auth pages ────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    user = authenticate_user(db, form.get("email", ""), form.get("password", ""))
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid email or password"})
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=302)

@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("signup.html", {"request": request, "error": None})

@app.post("/signup", response_class=HTMLResponse)
async def signup_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    email = form.get("email", "").strip()
    password = form.get("password", "")
    name = form.get("name", "").strip()
    if not email or not password:
        return templates.TemplateResponse("signup.html", {"request": request, "error": "Email and password are required"})
    if len(password) < 6:
        return templates.TemplateResponse("signup.html", {"request": request, "error": "Password must be at least 6 characters"})
    if db.query(User).filter(User.email == email.lower()).first():
        return templates.TemplateResponse("signup.html", {"request": request, "error": "An account with this email already exists"})
    user = create_user(db, email, password, name)
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=302)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ──── Page routes ────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    projects = user_projects(db, user.id, archived=False)
    project_data = []
    for p in projects:
        progress = calc_project_progress(db, p.id)
        info = get_days_info(p)
        task_count = db.query(Task).filter(Task.project_id == p.id).count()
        completed_count = db.query(Task).filter(Task.project_id == p.id, Task.is_completed == True).count()
        member_count = db.query(ProjectMember).filter(ProjectMember.project_id == p.id).count()
        project_data.append({"project": p, "progress": round(progress, 1),
                             "task_count": task_count, "completed_count": completed_count,
                             "member_count": member_count, **info})

    groups, ungrouped = {}, []
    for pd in project_data:
        g = pd["project"].group_name
        if g:
            groups.setdefault(g, []).append(pd)
        else:
            ungrouped.append(pd)

    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "groups": groups,
        "ungrouped": ungrouped, "today": date.today()
    })

@app.get("/project/{project_id}", response_class=HTMLResponse)
async def project_detail(request: Request, project_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    project, membership = get_user_project(db, user.id, project_id)

    progress = calc_project_progress(db, project_id)
    members_map = get_project_members_map(db, project_id)
    task_tree = get_task_tree(db, project_id, members_map)
    info = get_days_info(project)
    daily_target = None
    if info["days_left"] and info["days_left"] > 0:
        daily_target = round((100 - progress) / info["days_left"], 1)

    members_list = sorted(members_map.values(), key=lambda m: (0 if m["role"] == "owner" else 1, m["name"]))

    return templates.TemplateResponse("project.html", {
        "request": request, "user": user, "project": project,
        "membership": membership, "progress": round(progress, 1),
        "task_tree": task_tree, "daily_target": daily_target,
        "members": members_list, "members_map": members_map,
        "member_colors": MEMBER_COLORS, "can_edit": can_edit(membership),
        **info
    })

@app.get("/today", response_class=HTMLResponse)
async def today_view(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    today_date = date.today()
    my_project_ids = [p.id for p in user_projects(db, user.id, archived=False)]

    # Task IDs assigned to me
    my_task_ids = [ta.task_id for ta in db.query(TaskAssignee.task_id).filter(
        TaskAssignee.user_id == user.id
    ).all()] if my_project_ids else []

    # Tasks assigned to me
    assigned_to_me = db.query(Task).filter(
        Task.id.in_(my_task_ids), Task.is_completed == False,
        Task.project_id.in_(my_project_ids)
    ).all() if my_task_ids else []

    # Unassigned task IDs (tasks with no assignees)
    assigned_task_ids = [ta.task_id for ta in db.query(TaskAssignee.task_id).distinct().filter(
        TaskAssignee.task_id.in_(
            db.query(Task.id).filter(Task.project_id.in_(my_project_ids))
        )
    ).all()] if my_project_ids else []

    # Overdue
    overdue = db.query(Task).filter(
        Task.project_id.in_(my_project_ids),
        Task.due_date < today_date, Task.is_completed == False,
        or_(Task.id.in_(my_task_ids), ~Task.id.in_(assigned_task_ids)) if assigned_task_ids else True
    ).all() if my_project_ids else []

    # Due today
    due_today = db.query(Task).filter(
        Task.project_id.in_(my_project_ids),
        Task.due_date == today_date, Task.is_completed == False,
        or_(Task.id.in_(my_task_ids), ~Task.id.in_(assigned_task_ids)) if assigned_task_ids else True
    ).all() if my_project_ids else []

    # Active projects
    active_projects = user_projects(db, user.id, archived=False)
    project_progress = []
    for p in active_projects:
        prog = calc_project_progress(db, p.id)
        pinfo = get_days_info(p)
        incomplete = db.query(Task).filter(
            Task.project_id == p.id, Task.is_completed == False, Task.parent_task_id.is_(None)
        ).count()
        dt = round((100 - prog) / pinfo["days_left"], 1) if pinfo["days_left"] and pinfo["days_left"] > 0 else None
        member_count = db.query(ProjectMember).filter(ProjectMember.project_id == p.id).count()
        project_progress.append({"project": p, "progress": round(prog, 1),
                                 "daily_target": dt, "incomplete_tasks": incomplete,
                                 "member_count": member_count, **pinfo})

    return templates.TemplateResponse("today.html", {
        "request": request, "user": user, "today": today_date,
        "assigned_to_me": assigned_to_me, "due_today": due_today,
        "overdue": overdue, "project_progress": project_progress
    })

@app.get("/archive", response_class=HTMLResponse)
async def archive_view(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    projects = user_projects(db, user.id, archived=True)
    data = [{"project": p, "progress": round(calc_project_progress(db, p.id), 1)} for p in projects]
    return templates.TemplateResponse("archive.html", {"request": request, "user": user, "projects": data})

@app.get("/stats", response_class=HTMLResponse)
async def stats_view(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    thirty_ago = date.today() - timedelta(days=30)
    activities = db.query(ActivityLog.date, sqlfunc.count(ActivityLog.id)).filter(
        ActivityLog.user_id == user.id, ActivityLog.date >= thirty_ago
    ).group_by(ActivityLog.date).all()
    amap = {a[0].isoformat(): a[1] for a in activities}

    days = []
    for i in range(29, -1, -1):
        d = date.today() - timedelta(days=i)
        days.append({"date": d.isoformat(), "count": amap.get(d.isoformat(), 0), "label": d.strftime("%b %d")})

    streak = 0
    d = date.today()
    while amap.get(d.isoformat(), 0) > 0:
        streak += 1
        d -= timedelta(days=1)

    my_pids = [p.id for p in user_projects(db, user.id)]
    total_projects = len(my_pids)
    total_tasks = db.query(Task).filter(Task.project_id.in_(my_pids)).count() if my_pids else 0
    completed_tasks = db.query(Task).filter(Task.project_id.in_(my_pids), Task.is_completed == True).count() if my_pids else 0
    total_activities = db.query(ActivityLog).filter(ActivityLog.user_id == user.id).count()

    return templates.TemplateResponse("stats.html", {
        "request": request, "user": user, "days": days, "streak": streak,
        "total_projects": total_projects, "total_tasks": total_tasks,
        "completed_tasks": completed_tasks, "total_activities": total_activities
    })

@app.get("/search", response_class=HTMLResponse)
async def search_view(request: Request, q: str = "", db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    results = []
    if q.strip():
        query = f"%{q.strip()}%"
        tasks = db.query(Task).join(Project).join(ProjectMember).filter(
            ProjectMember.user_id == user.id,
            or_(Task.title.ilike(query), Task.notes.ilike(query))
        ).limit(30).all()
        projects = db.query(Project).join(ProjectMember).filter(
            ProjectMember.user_id == user.id,
            or_(Project.name.ilike(query), Project.description.ilike(query))
        ).limit(10).all()
        results = {"tasks": tasks, "projects": projects}
    return templates.TemplateResponse("search.html", {"request": request, "user": user, "q": q, "results": results})


# ──── Project API ────

@app.post("/api/projects")
async def create_project(data: ProjectCreate, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    p = Project(owner_id=user.id, name=data.name, description=data.description,
                color=data.color, start_date=parse_date(data.start_date),
                due_date=parse_date(data.due_date), group_name=data.group_name)
    db.add(p)
    db.flush()
    # Auto-add creator as owner
    db.add(ProjectMember(project_id=p.id, user_id=user.id, role="owner"))
    db.commit()
    db.refresh(p)
    log_activity(db, user.id, p.id, "project_created", p.name)
    return {"id": p.id, "name": p.name}

@app.put("/api/projects/{project_id}")
async def update_project(project_id: int, data: ProjectUpdate, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    project, mem = get_user_project(db, user.id, project_id)
    if not can_edit(mem):
        raise HTTPException(status_code=403, detail="View-only access")
    if data.name is not None: project.name = data.name
    if data.description is not None: project.description = data.description
    if data.color is not None: project.color = data.color
    if data.start_date is not None: project.start_date = parse_date(data.start_date)
    if data.due_date is not None: project.due_date = parse_date(data.due_date)
    if data.is_archived is not None: project.is_archived = data.is_archived
    if data.group_name is not None: project.group_name = data.group_name
    db.commit()
    return {"ok": True}

@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    project, mem = get_user_project(db, user.id, project_id)
    if mem.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can delete")
    db.delete(project)
    db.commit()
    return {"ok": True}


# ──── Member API ────

@app.post("/api/projects/{project_id}/members")
async def invite_member(project_id: int, data: MemberInvite, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    project, mem = get_user_project(db, user.id, project_id)
    if mem.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can invite members")

    target = db.query(User).filter(User.email == data.email.lower().strip()).first()
    if not target:
        raise HTTPException(status_code=404, detail="No user found with that email. They need to sign up first.")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="You're already in this project")

    existing = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id, ProjectMember.user_id == target.id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="User is already a member")

    role = data.role if data.role in ("editor", "viewer") else "editor"
    db.add(ProjectMember(project_id=project_id, user_id=target.id, role=role))
    db.commit()
    log_activity(db, user.id, project_id, "member_invited", f"{target.display_name} as {role}")
    return {"ok": True, "name": target.display_name, "email": target.email}

@app.put("/api/projects/{project_id}/members/{member_user_id}")
async def update_member(project_id: int, member_user_id: int, data: MemberUpdate, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    _, mem = get_user_project(db, user.id, project_id)
    if mem.role != "owner":
        raise HTTPException(status_code=403)
    if member_user_id == user.id:
        raise HTTPException(status_code=400, detail="Can't change your own role")

    target_mem = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id, ProjectMember.user_id == member_user_id
    ).first()
    if not target_mem:
        raise HTTPException(status_code=404)
    if data.role in ("editor", "viewer"):
        target_mem.role = data.role
    db.commit()
    return {"ok": True}

@app.delete("/api/projects/{project_id}/members/{member_user_id}")
async def remove_member(project_id: int, member_user_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    _, mem = get_user_project(db, user.id, project_id)

    # Owner can remove anyone; members can remove themselves
    if mem.role != "owner" and member_user_id != user.id:
        raise HTTPException(status_code=403)
    if member_user_id == user.id and mem.role == "owner":
        raise HTTPException(status_code=400, detail="Owner can't leave. Transfer ownership or delete the project.")

    target_mem = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id, ProjectMember.user_id == member_user_id
    ).first()
    if not target_mem:
        raise HTTPException(status_code=404)

    # Unassign tasks from removed member
    task_ids_in_project = [t.id for t in db.query(Task.id).filter(Task.project_id == project_id).all()]
    if task_ids_in_project:
        db.query(TaskAssignee).filter(
            TaskAssignee.task_id.in_(task_ids_in_project),
            TaskAssignee.user_id == member_user_id
        ).delete(synchronize_session=False)
    db.delete(target_mem)
    db.commit()
    return {"ok": True}


# ──── Task API ────

@app.post("/api/projects/{project_id}/tasks")
async def create_task(project_id: int, data: TaskCreate, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    project, mem = get_user_project(db, user.id, project_id)
    if not can_edit(mem):
        raise HTTPException(status_code=403)

    depth, pid = 0, data.parent_task_id
    while pid:
        depth += 1
        if depth > 6:
            raise HTTPException(status_code=400, detail="Maximum nesting depth is 6")
        p = db.query(Task).filter(Task.id == pid).first()
        pid = p.parent_task_id if p else None

    max_order = db.query(sqlfunc.max(Task.sort_order)).filter(
        Task.project_id == project_id, Task.parent_task_id == data.parent_task_id
    ).scalar() or 0

    t = Task(project_id=project_id, parent_task_id=data.parent_task_id,
             title=data.title, notes=data.notes, due_date=parse_date(data.due_date),
             sort_order=max_order + 1)
    db.add(t)
    db.flush()
    # Add assignees
    if data.assigned_to:
        for uid in data.assigned_to:
            if uid and db.query(ProjectMember).filter(
                ProjectMember.project_id == project_id, ProjectMember.user_id == uid
            ).first():
                db.add(TaskAssignee(task_id=t.id, user_id=uid))
    db.commit()
    db.refresh(t)
    log_activity(db, user.id, project_id, "task_created", t.title)
    return {"id": t.id, "title": t.title}

@app.put("/api/tasks/{task_id}")
async def update_task(task_id: int, data: TaskUpdate, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    t = get_user_task(db, user.id, task_id)
    # Check edit permission
    mem = db.query(ProjectMember).filter(
        ProjectMember.project_id == t.project_id, ProjectMember.user_id == user.id
    ).first()
    if not can_edit(mem):
        raise HTTPException(status_code=403)

    if data.title is not None: t.title = data.title
    if data.notes is not None: t.notes = data.notes
    if data.due_date is not None: t.due_date = parse_date(data.due_date)
    if data.assigned_to is not None:
        # Clear existing assignees and set new ones
        db.query(TaskAssignee).filter(TaskAssignee.task_id == t.id).delete(synchronize_session=False)
        for uid in data.assigned_to:
            if uid and db.query(ProjectMember).filter(
                ProjectMember.project_id == t.project_id, ProjectMember.user_id == uid
            ).first():
                db.add(TaskAssignee(task_id=t.id, user_id=uid))

    if data.is_completed is not None:
        t.is_completed = data.is_completed
        t.progress = 100.0 if data.is_completed else 0.0
        def set_children(pid, completed):
            for c in db.query(Task).filter(Task.parent_task_id == pid).all():
                c.is_completed = completed
                c.progress = 100.0 if completed else 0.0
                set_children(c.id, completed)
        set_children(t.id, data.is_completed)
        if data.is_completed:
            log_activity(db, user.id, t.project_id, "task_completed", t.title)

    if data.progress is not None:
        t.progress = min(100.0, max(0.0, data.progress))
        t.is_completed = t.progress >= 100.0
        log_activity(db, user.id, t.project_id, "progress_updated", f"{t.title}: {t.progress}%")

    db.commit()
    if t.parent_task_id:
        parent = db.query(Task).get(t.parent_task_id)
        if parent:
            recalc_task_progress(db, parent)

    return {"ok": True, "project_progress": round(calc_project_progress(db, t.project_id), 1),
            "task_progress": round(t.progress, 1)}

@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    t = get_user_task(db, user.id, task_id)
    mem = db.query(ProjectMember).filter(
        ProjectMember.project_id == t.project_id, ProjectMember.user_id == user.id
    ).first()
    if not can_edit(mem):
        raise HTTPException(status_code=403)
    project_id, parent_id = t.project_id, t.parent_task_id
    db.delete(t)
    db.commit()
    if parent_id:
        parent = db.query(Task).get(parent_id)
        if parent:
            recalc_task_progress(db, parent)
    return {"ok": True, "project_progress": round(calc_project_progress(db, project_id), 1)}


# ──── Comment API ────

@app.get("/api/tasks/{task_id}/comments")
async def get_comments(task_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    t = get_user_task(db, user.id, task_id)
    comments = db.query(Comment).options(joinedload(Comment.user)).filter(
        Comment.task_id == task_id
    ).order_by(Comment.created_at.asc()).all()
    return [{
        "id": c.id,
        "text": c.text,
        "user_name": c.user.display_name,
        "user_initials": c.user.initials,
        "user_id": c.user_id,
        "created_at": c.created_at.strftime("%b %d, %I:%M %p") if c.created_at else "",
    } for c in comments]

@app.post("/api/tasks/{task_id}/comments")
async def add_comment(task_id: int, data: CommentCreate, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    t = get_user_task(db, user.id, task_id)
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="Comment cannot be empty")
    comment = Comment(task_id=task_id, user_id=user.id, text=data.text.strip())
    db.add(comment)
    db.commit()
    db.refresh(comment)
    log_activity(db, user.id, t.project_id, "comment_added", f"on {t.title}")
    return {
        "id": comment.id, "text": comment.text,
        "user_name": user.display_name, "user_initials": user.initials,
        "user_id": user.id,
        "created_at": comment.created_at.strftime("%b %d, %I:%M %p") if comment.created_at else "",
    }

@app.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404)
    # Only comment author or project owner can delete
    t = db.query(Task).filter(Task.id == comment.task_id).first()
    mem = db.query(ProjectMember).filter(
        ProjectMember.project_id == t.project_id, ProjectMember.user_id == user.id
    ).first()
    if not mem:
        raise HTTPException(status_code=404)
    if comment.user_id != user.id and mem.role != "owner":
        raise HTTPException(status_code=403, detail="Can only delete your own comments")
    db.delete(comment)
    db.commit()
    return {"ok": True}


@app.get("/api/export")
async def export_data(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    projects = user_projects(db, user.id)
    data = []
    for p in projects:
        tasks = db.query(Task).filter(Task.project_id == p.id).all()
        data.append({
            "name": p.name, "description": p.description, "color": p.color,
            "start_date": p.start_date.isoformat() if p.start_date else None,
            "due_date": p.due_date.isoformat() if p.due_date else None,
            "tasks": [{"title": t.title, "notes": t.notes, "progress": t.progress,
                       "is_completed": t.is_completed} for t in tasks]
        })
    return JSONResponse(data, headers={"Content-Disposition": "attachment; filename=progress-export.json"})


# ──── PWA ────

@app.get("/manifest.json")
async def manifest():
    return JSONResponse({
        "name": "Progress", "short_name": "Progress",
        "start_url": "/", "display": "standalone",
        "background_color": "#0f172a", "theme_color": "#6366f1",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
