"""Existing MVP tables and additive training tables (no destructive schema changes)."""
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Text, JSON, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def utcnow(): return datetime.now(timezone.utc)
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__='users'
    id:Mapped[int]=mapped_column(primary_key=True)
    username:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    password_hash:Mapped[str]
    role:Mapped[str]=mapped_column(String(16))
class Scenario(Base):
    __tablename__='scenarios'
    id:Mapped[int]=mapped_column(primary_key=True)
    title:Mapped[str]
    category:Mapped[str]
    caller_text:Mapped[str]=mapped_column(Text)
    expected:Mapped[dict]=mapped_column(JSON)
    created_by:Mapped[int|None]=mapped_column(ForeignKey('users.id'),nullable=True)
class SessionRun(Base):
    __tablename__='session_runs'
    id:Mapped[int]=mapped_column(primary_key=True)
    scenario_id:Mapped[int]=mapped_column(ForeignKey('scenarios.id'))
    student_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    started_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    finished_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    answers:Mapped[dict]=mapped_column(JSON,default=dict)
    score:Mapped[int|None]=mapped_column(nullable=True)
    report:Mapped[dict|None]=mapped_column(JSON,nullable=True)
class Audit(Base):
    __tablename__='audit'
    id:Mapped[int]=mapped_column(primary_key=True)
    at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    user_id:Mapped[int|None]=mapped_column(nullable=True)
    action:Mapped[str]
    details:Mapped[dict]=mapped_column(JSON,default=dict)
class ClassifierVersion(Base):
    __tablename__='classifier_versions'
    id:Mapped[int]=mapped_column(primary_key=True)
    sha256:Mapped[str]=mapped_column(String(64),unique=True)
    filename:Mapped[str]
    sheet:Mapped[str]
    imported_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    manifest:Mapped[dict]=mapped_column(JSON)
class IncidentType(Base):
    __tablename__='incident_types'
    __table_args__=(UniqueConstraint('version_id','code'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    version_id:Mapped[int]=mapped_column(ForeignKey('classifier_versions.id'),index=True)
    code:Mapped[str]=mapped_column(String(32),index=True)
    category:Mapped[str]
    title:Mapped[str]
    source_row:Mapped[int]
    data:Mapped[dict]=mapped_column(JSON)
class ScenarioSettings(Base):
    __tablename__='scenario_settings'
    scenario_id:Mapped[int]=mapped_column(ForeignKey('scenarios.id'),primary_key=True)
    published:Mapped[bool]=mapped_column(default=False)
    difficulty:Mapped[str]=mapped_column(default='basic')
    mode:Mapped[str]=mapped_column(default='call')
    initial_card:Mapped[dict]=mapped_column(JSON,default=dict)
    source:Mapped[dict]=mapped_column(JSON,default=dict)
class Material(Base):
    __tablename__='learning_materials'
    id:Mapped[int]=mapped_column(primary_key=True)
    source_key:Mapped[str]=mapped_column(unique=True)
    title:Mapped[str]
    source:Mapped[dict]=mapped_column(JSON)
    content:Mapped[dict]=mapped_column(JSON)
class Lesson(Base):
    __tablename__='lessons'
    id:Mapped[int]=mapped_column(primary_key=True)
    title:Mapped[str]
    teacher_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    status:Mapped[str]=mapped_column(default='prepared')
    mode:Mapped[str]=mapped_column(default='call')
    require_sip:Mapped[bool]=mapped_column(default=False,server_default='false')
    scenario_ids:Mapped[list]=mapped_column(JSON)
    student_ids:Mapped[list]=mapped_column(JSON)
    service_code:Mapped[str]
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    started_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
class RunContext(Base):
    __tablename__='run_contexts'
    run_id:Mapped[int]=mapped_column(ForeignKey('session_runs.id'),primary_key=True)
    lesson_id:Mapped[int|None]=mapped_column(ForeignKey('lessons.id'),nullable=True,index=True)
    scenario_snapshot:Mapped[dict]=mapped_column(JSON)
    registered_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    processed:Mapped[bool]=mapped_column(default=False)
    checked:Mapped[bool]=mapped_column(default=False)
    revision:Mapped[int]=mapped_column(default=0)
class CardEvent(Base):
    __tablename__='card_events'
    id:Mapped[int]=mapped_column(primary_key=True)
    run_id:Mapped[int]=mapped_column(ForeignKey('session_runs.id'),index=True)
    at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    kind:Mapped[str]
    data:Mapped[dict]=mapped_column(JSON)
class ExpertReview(Base):
    __tablename__='expert_reviews'
    id:Mapped[int]=mapped_column(primary_key=True)
    run_id:Mapped[int]=mapped_column(ForeignKey('session_runs.id'),index=True)
    teacher_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    score:Mapped[int]
    comment:Mapped[str]=mapped_column(Text)
    at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class AccountState(Base):
    __tablename__='account_states'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    blocked:Mapped[bool]=mapped_column(default=False)
class SipAccount(Base):
    __tablename__='sip_accounts'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    username:Mapped[str]=mapped_column(String(64),unique=True)
    password:Mapped[str]=mapped_column(String(128))
class VoipCall(Base):
    __tablename__='voip_calls'
    id:Mapped[int]=mapped_column(primary_key=True)
    run_id:Mapped[int]=mapped_column(ForeignKey('session_runs.id'),index=True)
    state:Mapped[str]=mapped_column(String(32),default='queued')
    sound_key:Mapped[str]=mapped_column(String(64))
    channel:Mapped[str|None]=mapped_column(String(256),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    answered_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    ended_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    error:Mapped[str|None]=mapped_column(Text,nullable=True)
class CardAttachment(Base):
    __tablename__='card_attachments'
    id:Mapped[int]=mapped_column(primary_key=True)
    run_id:Mapped[int]=mapped_column(ForeignKey('session_runs.id'),index=True)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    filename:Mapped[str]=mapped_column(String(255))
    stored_name:Mapped[str]=mapped_column(String(255),unique=True)
    content_type:Mapped[str]=mapped_column(String(100))
    size:Mapped[int]
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class AiJob(Base):
    __tablename__='ai_jobs'
    id:Mapped[int]=mapped_column(primary_key=True)
    teacher_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    status:Mapped[str]=mapped_column(String(32),default='queued')
    input:Mapped[dict]=mapped_column(JSON)
    result:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    error:Mapped[str|None]=mapped_column(Text,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)

class OperatorPresence(Base):
    __tablename__='operator_presence'
    user_id:Mapped[int]=mapped_column(primary_key=True)
    state:Mapped[str]=mapped_column(String(32),default='unavailable')
    available_after:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)

class TrainingMessage(Base):
    __tablename__='training_messages'
    id:Mapped[int]=mapped_column(primary_key=True)
    student_id:Mapped[int]=mapped_column(index=True)
    teacher_id:Mapped[int]=mapped_column(index=True)
    scenario_id:Mapped[int]=mapped_column(ForeignKey('scenarios.id'))
    aon:Mapped[str]=mapped_column(String(100))
    text:Mapped[str]=mapped_column(Text)
    coordinates:Mapped[dict]=mapped_column(JSON,default=dict)
    run_id:Mapped[int|None]=mapped_column(nullable=True,index=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    read:Mapped[bool]=mapped_column(default=False)

class TrainingIssue(Base):
    __tablename__='training_issues'
    id:Mapped[int]=mapped_column(primary_key=True)
    user_id:Mapped[int]=mapped_column(index=True)
    run_id:Mapped[int|None]=mapped_column(nullable=True)
    description:Mapped[str]=mapped_column(Text)
    attachment:Mapped[str|None]=mapped_column(nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
