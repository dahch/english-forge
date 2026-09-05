from __future__ import annotations

import uuid
from datetime import datetime, date

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    current_level: Mapped[str] = mapped_column(String(2), nullable=False, default="B1")
    assessment_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    vocab_items: Mapped[list["VocabItem"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    scenarios: Mapped[list["Scenario"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    progress_entries: Mapped[list["ProgressDaily"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    settings: Mapped[list["Setting"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    provider_configs: Mapped[list["ProviderConfig"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    tutor_profile: Mapped["TutorProfile | None"] = relationship(back_populates="user", cascade="all, delete-orphan", uselist=False)
    assessments: Mapped[list["Assessment"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    generated_lessons: Mapped[list["GeneratedLesson"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    learning_paths: Mapped[list["LearningPath"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class TutorProfile(Base):
    __tablename__ = "tutor_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Sarah")
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(50), nullable=True)
    personality: Mapped[str] = mapped_column(String(100), nullable=False, default="friendly")
    voice: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="tutor_profile")


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    cefr_level: Mapped[str] = mapped_column(String(2), nullable=False, default="B1")
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="scenarios")
    sessions: Mapped[list["Session"]] = relationship(back_populates="scenario")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scenario_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cefr_level: Mapped[str] = mapped_column(String(2), nullable=False, default="B1")
    provider_used: Mapped[str | None] = mapped_column(String(100), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")
    scenario: Mapped["Scenario | None"] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    audio_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped["Session"] = relationship(back_populates="messages")
    corrections: Mapped[list["Correction"]] = relationship(back_populates="message", cascade="all, delete-orphan")


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    error_type: Mapped[str] = mapped_column(String(100), nullable=False)
    original_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    correction: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")

    message: Mapped["Message"] = relationship(back_populates="corrections")


class VocabItem(Base):
    __tablename__ = "vocab_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    word: Mapped[str] = mapped_column(String(200), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False, default="")
    example: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ipa: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ease_factor: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[int] = mapped_column(Integer, default=0)
    next_review_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="vocab_items")


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    estimated_level: Mapped[str | None] = mapped_column(String(2), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    strengths: Mapped[str | None] = mapped_column(Text, nullable=True)
    weaknesses: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendations: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="assessments")
    messages: Mapped[list["AssessmentMessage"]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", order_by="AssessmentMessage.created_at"
    )
    learning_paths: Mapped[list["LearningPath"]] = relationship(back_populates="assessment")


class AssessmentMessage(Base):
    __tablename__ = "assessment_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(String(36), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    assessment: Mapped["Assessment"] = relationship(back_populates="messages")


class GeneratedLesson(Base):
    __tablename__ = "generated_lessons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=True)
    level: Mapped[str] = mapped_column(String(2), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=True)
    examples: Mapped[str | None] = mapped_column(Text, nullable=True)
    exercises: Mapped[str | None] = mapped_column(Text, nullable=True)
    based_on_errors: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="generated_lessons")


class LearningPath(Base):
    __tablename__ = "learning_paths"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assessment_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("assessments.id", ondelete="SET NULL"), nullable=True)
    current_level: Mapped[str] = mapped_column(String(2), nullable=False)
    target_level: Mapped[str] = mapped_column(String(2), nullable=False)
    lessons_required: Mapped[int] = mapped_column(Integer, nullable=False)
    lessons_completed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="learning_paths")
    assessment: Mapped["Assessment | None"] = relationship(back_populates="learning_paths")
    path_lessons: Mapped[list["PathLesson"]] = relationship(back_populates="learning_path", cascade="all, delete-orphan", order_by="PathLesson.order")


class PathLesson(Base):
    __tablename__ = "path_lessons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    path_id: Mapped[str] = mapped_column(String(36), ForeignKey("learning_paths.id", ondelete="CASCADE"), nullable=False)
    lesson_type: Mapped[str] = mapped_column(String(50), nullable=False)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    learning_path: Mapped["LearningPath"] = relationship(back_populates="path_lessons")


class ProgressDaily(Base):
    __tablename__ = "progress_daily"
    __table_args__ = (UniqueConstraint("date", "user_id", name="uq_progress_daily_date_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    minutes_spoken: Mapped[int] = mapped_column(Integer, default=0)
    new_words: Mapped[int] = mapped_column(Integer, default=0)
    reviews_done: Mapped[int] = mapped_column(Integer, default=0)
    streak_count: Mapped[int] = mapped_column(Integer, default=0)
    lessons_completed: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship(back_populates="progress_entries")


class Setting(Base):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("key", "user_id", name="uq_settings_key_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")

    user: Mapped["User"] = relationship(back_populates="settings")


class ProviderConfig(Base):
    __tablename__ = "provider_configs"
    __table_args__ = (UniqueConstraint("provider_name", "user_id", name="uq_provider_config_name_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    protocol: Mapped[str] = mapped_column(String(20), nullable=False, default="openai")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    task_routing: Mapped[str] = mapped_column(String(100), nullable=False, default="conversation,correction,lesson")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="provider_configs")
