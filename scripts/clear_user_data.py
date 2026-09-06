#!/usr/bin/env python3
"""
Standalone script to clear all user data from the database for testing purposes.

Usage:
    python scripts/clear_user_data.py --email user@example.com
    python scripts/clear_user_data.py --email user@example.com --dry-run

This script deletes:
- Sessions and messages
- Corrections
- Vocabulary items
- Scenarios
- Assessments and assessment messages
- Generated lessons
- Learning paths and path lessons
- Progress entries
- Settings

It preserves:
- The user account itself
- Provider configurations (LLM API keys)
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add the backend directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.models import (
    Assessment,
    AssessmentMessage,
    Correction,
    GeneratedLesson,
    LearningPath,
    Message,
    PathLesson,
    ProgressDaily,
    Scenario,
    Session,
    Setting,
    User,
    VocabItem,
)


async def clear_user_data(email: str, dry_run: bool = False):
    from app.config import get_settings

    engine = create_async_engine(get_settings().DATABASE_URL)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Find the user
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user:
            print(f"Error: User with email '{email}' not found")
            await engine.dispose()
            return False

        user_id = user.id
        print(f"Found user: {user.email} (ID: {user_id})")

        if dry_run:
            print("\n[DRY RUN] Would delete the following:")

            # Count what would be deleted
            session_count = (await db.execute(select(Session).where(Session.user_id == user_id))).scalars().all()
            message_count = (await db.execute(select(Message).where(Message.session_id.in_(
                select(Session.id).where(Session.user_id == user_id)
            )))).scalars().all()
            correction_count = (await db.execute(select(Correction).where(Correction.message_id.in_(
                select(Message.id).where(Message.session_id.in_(
                    select(Session.id).where(Session.user_id == user_id)
                ))
            )))).scalars().all()
            vocab_count = (await db.execute(select(VocabItem).where(VocabItem.user_id == user_id))).scalars().all()
            scenario_count = (await db.execute(select(Scenario).where(Scenario.user_id == user_id))).scalars().all()
            assessment_count = (await db.execute(select(Assessment).where(Assessment.user_id == user_id))).scalars().all()
            lesson_count = (await db.execute(select(GeneratedLesson).where(GeneratedLesson.user_id == user_id))).scalars().all()
            path_count = (await db.execute(select(LearningPath).where(LearningPath.user_id == user_id))).scalars().all()
            progress_count = (await db.execute(select(ProgressDaily).where(ProgressDaily.user_id == user_id))).scalars().all()
            setting_count = (await db.execute(select(Setting).where(Setting.user_id == user_id))).scalars().all()

            print(f"  - {len(session_count)} sessions")
            print(f"  - {len(message_count)} messages")
            print(f"  - {len(correction_count)} corrections")
            print(f"  - {len(vocab_count)} vocabulary items")
            print(f"  - {len(scenario_count)} scenarios")
            print(f"  - {len(assessment_count)} assessments")
            print(f"  - {len(lesson_count)} generated lessons")
            print(f"  - {len(path_count)} learning paths")
            print(f"  - {len(progress_count)} progress entries")
            print(f"  - {len(setting_count)} settings")
            print("\nUser account and provider configs will be preserved.")
            await engine.dispose()
            return True

        # Delete in order to respect foreign key constraints
        print("Deleting data...")

        # Delete corrections first (they reference messages)
        await db.execute(
            delete(Correction).where(Correction.message_id.in_(
                select(Message.id).where(Message.session_id.in_(
                    select(Session.id).where(Session.user_id == user_id)
                ))
            ))
        )
        print("  - Deleted corrections")

        # Delete messages
        await db.execute(
            delete(Message).where(Message.session_id.in_(
                select(Session.id).where(Session.user_id == user_id)
            ))
        )
        print("  - Deleted messages")

        # Delete sessions
        await db.execute(delete(Session).where(Session.user_id == user_id))
        print("  - Deleted sessions")

        # Delete vocab items
        await db.execute(delete(VocabItem).where(VocabItem.user_id == user_id))
        print("  - Deleted vocabulary items")

        # Delete scenarios
        await db.execute(delete(Scenario).where(Scenario.user_id == user_id))
        print("  - Deleted scenarios")

        # Delete assessment messages
        await db.execute(
            delete(AssessmentMessage).where(AssessmentMessage.assessment_id.in_(
                select(Assessment.id).where(Assessment.user_id == user_id)
            ))
        )
        print("  - Deleted assessment messages")

        # Delete assessments
        await db.execute(delete(Assessment).where(Assessment.user_id == user_id))
        print("  - Deleted assessments")

        # Delete generated lessons
        await db.execute(delete(GeneratedLesson).where(GeneratedLesson.user_id == user_id))
        print("  - Deleted generated lessons")

        # Delete path lessons
        await db.execute(
            delete(PathLesson).where(PathLesson.path_id.in_(
                select(LearningPath.id).where(LearningPath.user_id == user_id)
            ))
        )
        print("  - Deleted path lessons")

        # Delete learning paths
        await db.execute(delete(LearningPath).where(LearningPath.user_id == user_id))
        print("  - Deleted learning paths")

        # Delete progress entries
        await db.execute(delete(ProgressDaily).where(ProgressDaily.user_id == user_id))
        print("  - Deleted progress entries")

        # Delete settings
        await db.execute(delete(Setting).where(Setting.user_id == user_id))
        print("  - Deleted settings")

        # Reset user level and assessment status
        user.current_level = "B1"
        user.assessment_completed = False
        print("  - Reset user level to B1 and assessment status")

        await db.commit()
        print("\nAll user data cleared successfully!")
        print("User account and provider configs preserved.")

        await engine.dispose()
        return True


def main():
    parser = argparse.ArgumentParser(description="Clear all user data for testing")
    parser.add_argument("--email", required=True, help="User email to clear data for")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without actually deleting")

    args = parser.parse_args()

    success = asyncio.run(clear_user_data(args.email, args.dry_run))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
