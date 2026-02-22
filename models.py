from sqlalchemy import Column, Integer, String, Text, Float, Boolean, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "prog_users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    memberships = relationship("ProjectMember", back_populates="user", cascade="all, delete-orphan")
    activities = relationship("ActivityLog", back_populates="user", cascade="all, delete-orphan")
    task_assignments = relationship("TaskAssignee", back_populates="user", cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="user", cascade="all, delete-orphan")

    @property
    def initials(self):
        if self.name:
            parts = self.name.strip().split()
            return "".join(p[0].upper() for p in parts[:2])
        return self.email[0].upper()

    @property
    def display_name(self):
        return self.name or self.email.split("@")[0]


class ProjectMember(Base):
    __tablename__ = "prog_project_members"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("prog_projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("prog_users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), default="editor")
    joined_at = Column(DateTime(timezone=True), server_default=func.now())

    project = relationship("Project", back_populates="members")
    user = relationship("User", back_populates="memberships")

    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_prog_project_user"),)


class Project(Base):
    __tablename__ = "prog_projects"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("prog_users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    color = Column(String(7), default="#6366f1")
    start_date = Column(Date, nullable=True)
    due_date = Column(Date, nullable=True)
    is_archived = Column(Boolean, default=False)
    group_name = Column(String(255), default="")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner = relationship("User", foreign_keys=[owner_id])
    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan", foreign_keys="Task.project_id")


class Task(Base):
    __tablename__ = "prog_tasks"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("prog_projects.id", ondelete="CASCADE"), nullable=False)
    parent_task_id = Column(Integer, ForeignKey("prog_tasks.id", ondelete="CASCADE"), nullable=True)
    title = Column(String(500), nullable=False)
    notes = Column(Text, default="")
    progress = Column(Float, default=0.0)
    due_date = Column(Date, nullable=True)
    is_completed = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    project = relationship("Project", back_populates="tasks", foreign_keys=[project_id])
    parent = relationship("Task", remote_side=[id], backref="subtasks")
    assignees = relationship("TaskAssignee", back_populates="task", cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="task", cascade="all, delete-orphan",
                            order_by="Comment.created_at")


class TaskAssignee(Base):
    __tablename__ = "prog_task_assignees"
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("prog_tasks.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("prog_users.id", ondelete="CASCADE"), nullable=False)

    task = relationship("Task", back_populates="assignees")
    user = relationship("User", back_populates="task_assignments")

    __table_args__ = (UniqueConstraint("task_id", "user_id", name="uq_prog_task_assignee"),)


class Comment(Base):
    __tablename__ = "prog_comments"
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("prog_tasks.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("prog_users.id", ondelete="CASCADE"), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    task = relationship("Task", back_populates="comments")
    user = relationship("User", back_populates="comments")


class ActivityLog(Base):
    __tablename__ = "prog_activity_log"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("prog_users.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(Integer, ForeignKey("prog_projects.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(50), nullable=False)
    detail = Column(String(500), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    date = Column(Date, server_default=func.current_date())

    user = relationship("User", back_populates="activities")
