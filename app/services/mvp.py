from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import Date, and_, cast, func, or_, select
from sqlalchemy.orm import selectinload

from app.db.database import async_session, create_db_and_tables
from app.db.models import (
    Announcement,
    ChatMessage,
    Deadline,
    Homework,
    HomeworkCompletion,
    MoodEntry,
    LinkStatus,
    ParentStudentLink,
    ReminderDelivery,
    Reward,
    ScheduleItem,
    StressSignal,
    User,
)

STRESS_TERMS: dict[str, tuple[str, ...]] = {
    "усталость": ("устал", "не высыпаюсь", "нет сил", "выгорел"),
    "тревога": ("страшно", "тревож", "паника", "боюсь"),
    "конфликты": ("ненавижу", "ругаемся", "ссор", "буллинг"),
    "одиночество": ("одиноко", "никому не нужен", "нет друзей"),
    "безнадёжность": ("не вижу смысла", "не могу больше", "всё бессмысленно"),
    "раздражение": ("всё бесит", "достало", "раздраж"),
}


async def ensure_database() -> None:
    await create_db_and_tables()


async def user_by_max_id(max_user_id: str) -> User | None:
    await ensure_database()
    async with async_session() as session:
        return await session.scalar(
            select(User).where(User.max_user_id == int(max_user_id))
        )


async def update_profile(max_user_id: str, **values: object) -> User:
    await ensure_database()
    async with async_session() as session:
        user = await session.scalar(
            select(User).where(User.max_user_id == int(max_user_id))
        )
        if user is None:
            user = User(max_user_id=int(max_user_id), role="unselected", name="Пользователь")
            session.add(user)
        original_role = getattr(user.role, "value", user.role)
        requested_role = values.get("role")
        requested_role = getattr(requested_role, "value", requested_role)
        role_transition = (
            original_role == "unselected"
            and requested_role is not None
            and requested_role != "unselected"
        )
        expected_status = None
        if role_transition:
            expected_status = (
                "pending"
                if requested_role in {"teacher", "psychologist"}
                else "approved"
            )
            requested_status = values.get("status")
            requested_status = getattr(requested_status, "value", requested_status)
            if requested_status is not None and requested_status != expected_status:
                raise ValueError("The initial status is determined by the selected role")

        for key, value in values.items():
            if not hasattr(user, key):
                raise ValueError(f"Unknown profile field: {key}")
            if (
                key == "role"
                and original_role != "unselected"
                and original_role != getattr(value, "value", value)
            ):
                raise ValueError("A registered user's role cannot be changed")
            if key == "status":
                status_value = getattr(value, "value", value)
                current_status = getattr(user.status, "value", user.status)
                if role_transition:
                    if status_value != expected_status:
                        raise ValueError(
                            "The initial status is determined by the selected role"
                        )
                elif status_value != current_status:
                    raise ValueError("Only an administrator can change user status")
                continue
            setattr(user, key, value)
        if role_transition:
            assert expected_status is not None
            user.status = expected_status
        await session.commit()
        await session.refresh(user)
        return user


async def user_by_id(user_id: int) -> User | None:
    await ensure_database()
    async with async_session() as session:
        return await session.get(User, user_id)


async def link_parent_to_child(parent: User, child_max_id: str) -> bool:
    from app.db.crud import request_parent_student_link, student_candidates

    candidates = await student_candidates(child_max_id)
    if len(candidates) != 1:
        return False
    _, created = await request_parent_student_link(parent.id, candidates[0].id)
    return created


async def student_announcements(student: User) -> list[Announcement]:
    await ensure_database()
    if not student.class_name:
        return []
    async with async_session() as session:
        rows = await session.scalars(
            select(Announcement)
            .join(User, User.id == Announcement.teacher_id)
            .where(
                Announcement.class_name == student.class_name,
                User.school == student.school,
            )
            .order_by(Announcement.created_at.desc())
            .limit(10)
        )
        return list(rows)


async def create_schedule_item(
    owner_id: int,
    creator_id: int,
    day_of_week: int,
    lesson_time: time,
    subject: str,
    room: str | None = None,
    class_name: str | None = None,
) -> ScheduleItem:
    await ensure_database()
    async with async_session() as session:
        item = ScheduleItem(
            user_id=owner_id,
            created_by=creator_id,
            day_of_week=day_of_week,
            time=lesson_time,
            subject=subject,
            room=room,
            class_name=class_name,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item


async def schedule_for_day(user: User, weekday: int) -> list[ScheduleItem]:
    await ensure_database()
    async with async_session() as session:
        conditions = [ScheduleItem.user_id == user.id]
        if user.class_name and user.school:
            conditions.append(
                and_(
                    ScheduleItem.class_name == user.class_name,
                    ScheduleItem.created_by.in_(
                        select(User.id).where(
                            User.role == "teacher", User.school == user.school
                        )
                    ),
                )
            )
        rows = await session.scalars(
            select(ScheduleItem)
            .where(ScheduleItem.day_of_week == weekday, or_(*conditions))
            .order_by(ScheduleItem.time)
        )
        return list(rows)


async def delete_schedule_item(user_id: int, item_id: int) -> bool:
    await ensure_database()
    async with async_session() as session:
        item = await session.get(ScheduleItem, item_id)
        if item is None or item.user_id != user_id or item.created_by != user_id:
            return False
        await session.delete(item)
        await session.commit()
        return True


async def create_homework(
    owner_id: int,
    creator_id: int,
    subject: str,
    description: str,
    deadline: datetime,
    class_name: str | None = None,
) -> Homework:
    await ensure_database()
    async with async_session() as session:
        item = Homework(
            user_id=owner_id,
            created_by=creator_id,
            subject=subject,
            description=description,
            deadline=deadline,
            class_name=class_name,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item


async def active_homework(user: User) -> list[tuple[Homework, bool]]:
    await ensure_database()
    async with async_session() as session:
        conditions = [and_(Homework.user_id == user.id, Homework.status == "active")]
        if user.class_name and user.school:
            conditions.append(
                and_(
                    Homework.class_name == user.class_name,
                    Homework.created_by != user.id,
                    Homework.status == "active",
                    Homework.created_by.in_(
                        select(User.id).where(
                            User.role == "teacher",
                            User.school == user.school,
                        )
                    ),
                )
            )
        items = list(
            await session.scalars(
                select(Homework).where(or_(*conditions)).order_by(Homework.deadline)
            )
        )
        completions = set(
            await session.scalars(
                select(HomeworkCompletion.homework_id).where(
                    HomeworkCompletion.student_id == user.id,
                    HomeworkCompletion.homework_id.in_([item.id for item in items] or [-1]),
                )
            )
        )
        return [(item, item.id in completions) for item in items]


async def complete_homework(user: User, homework_id: int) -> bool:
    await ensure_database()
    async with async_session() as session:
        item = await session.get(Homework, homework_id)
        if item is None or item.status != "active":
            return False
        if item.user_id != user.id and (
            not user.class_name
            or item.class_name != user.class_name
            or item.created_by == user.id
        ):
            return False
        if item.user_id != user.id:
            teacher = await session.get(User, item.created_by)
            if (
                teacher is None
                or teacher.role != "teacher"
                or not user.school
                or teacher.school != user.school
            ):
                return False
        completed = await session.scalar(
            select(HomeworkCompletion.id).where(
                HomeworkCompletion.homework_id == item.id,
                HomeworkCompletion.student_id == user.id,
            )
        )
        if completed is not None:
            return False
        if item.user_id == user.id:
            item.status = "done"
            item.completed_at = datetime.now()
        else:
            session.add(HomeworkCompletion(homework_id=item.id, student_id=user.id))
        student_row = await session.get(User, user.id)
        if student_row is None:
            return False
        student_row.points += 10
        await session.commit()
        return True


async def create_deadline(
    owner_id: int,
    creator_id: int,
    title: str,
    due: datetime,
    kind: str,
    class_name: str | None = None,
) -> Deadline:
    await ensure_database()
    async with async_session() as session:
        item = Deadline(
            user_id=owner_id,
            created_by=creator_id,
            title=title,
            date=due,
            kind=kind,
            class_name=class_name,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item


async def upcoming_deadlines(user: User) -> list[Deadline]:
    await ensure_database()
    now = datetime.now()
    async with async_session() as session:
        conditions = [Deadline.user_id == user.id]
        if user.class_name and user.school:
            conditions.append(
                and_(
                    Deadline.class_name == user.class_name,
                    Deadline.created_by.in_(
                        select(User.id).where(
                            User.role == "teacher", User.school == user.school
                        )
                    ),
                )
            )
        rows = await session.scalars(
            select(Deadline)
            .where(Deadline.date >= now, or_(*conditions))
            .order_by(Deadline.date)
            .limit(20)
        )
        return list(rows)


async def save_mood(user: User, score: int, comment: str | None = None) -> bool:
    if score not in range(1, 6):
        raise ValueError("Mood score must be between 1 and 5")
    await ensure_database()
    async with async_session() as session:
        exists = await session.scalar(
            select(MoodEntry.id).where(
                MoodEntry.user_id == user.id, MoodEntry.date == date.today()
            )
        )
        if exists is not None:
            return False
        session.add(
            MoodEntry(user_id=user.id, date=date.today(), score=score, comment=comment)
        )
        student_row = await session.get(User, user.id)
        if student_row is None:
            raise ValueError("Student profile no longer exists")
        student_row.points += 3
        await session.commit()
        return True


async def mood_history(user_id: int, days: int = 14) -> list[MoodEntry]:
    await ensure_database()
    async with async_session() as session:
        rows = await session.scalars(
            select(MoodEntry)
            .where(
                MoodEntry.user_id == user_id,
                MoodEntry.date >= date.today() - timedelta(days=days - 1),
            )
            .order_by(MoodEntry.date.desc())
        )
        return list(rows)


def analyze_stress(text: str) -> tuple[int, list[str]]:
    normalized = text.casefold()
    themes = [
        theme
        for theme, terms in STRESS_TERMS.items()
        if any(term in normalized for term in terms)
    ]
    if any(term in normalized for term in ("хочу умереть", "покончить с собой", "убить себя")):
        return 5, themes + ["требуется срочная поддержка"]
    if len(themes) >= 2:
        return 4, themes
    if themes:
        return 3, themes
    return 1, []


async def record_chat_message(user: User, text: str) -> tuple[int, list[str], bool]:
    score, themes = analyze_stress(text)
    await ensure_database()
    async with async_session() as session:
        session.add(
            ChatMessage(
                user_id=user.id,
                text=text,
                risk_score=score,
                themes=", ".join(themes) if themes else None,
            )
        )
        await session.commit()
        severe_days = list(
            await session.scalars(
                select(cast(ChatMessage.created_at, Date))
                .where(ChatMessage.user_id == user.id, ChatMessage.risk_score >= 4)
                .group_by(cast(ChatMessage.created_at, Date))
                .order_by(cast(ChatMessage.created_at, Date).desc())
                .limit(2)
            )
        )
    threshold = (
        score >= 4
        and len(severe_days) == 2
        and severe_days[0] == date.today()
        and severe_days[1] == date.today() - timedelta(days=1)
    )
    return score, themes, threshold


async def create_stress_signal(
    student: User, preferred_format: str
) -> StressSignal:
    if preferred_format not in {"chat", "live"}:
        raise ValueError("Unsupported support format")
    await ensure_database()
    async with async_session() as session:
        latest_message = await session.scalar(
            select(ChatMessage)
            .where(ChatMessage.user_id == student.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        psychologists = await session.scalars(
            select(User).where(
                User.role == "psychologist",
                User.school == student.school,
            )
        )
        psychologist = next(iter(psychologists), None)
        signal = StressSignal(
            student_id=student.id,
            psychologist_id=psychologist.id if psychologist else None,
            risk_level=latest_message.risk_score if latest_message and latest_message.risk_score else 4,
            themes=latest_message.themes if latest_message and latest_message.themes else "ученик запросил поддержку",
            status="pending",
            consent=True,
            preferred_format=preferred_format,
        )
        session.add(signal)
        await session.commit()
        await session.refresh(signal)
        return signal


async def list_stress_signals(psychologist: User) -> list[StressSignal]:
    await ensure_database()
    async with async_session() as session:
        conditions = [StressSignal.psychologist_id == psychologist.id]
        if psychologist.school:
            conditions.append(
                and_(
                    StressSignal.psychologist_id.is_(None),
                    StressSignal.student_id.in_(
                        select(User.id).where(User.school == psychologist.school)
                    ),
                )
            )
        rows = await session.scalars(
            select(StressSignal)
            .options(selectinload(StressSignal.student))
            .where(StressSignal.consent.is_(True), or_(*conditions))
            .order_by(StressSignal.created_at.desc())
        )
        return list(rows)


async def set_signal_status(psychologist: User, signal_id: int, status: str) -> bool:
    if status not in {"in_progress", "closed"}:
        raise ValueError("Unsupported signal status")
    await ensure_database()
    async with async_session() as session:
        signal = await session.scalar(
            select(StressSignal)
            .options(selectinload(StressSignal.student))
            .where(StressSignal.id == signal_id, StressSignal.consent.is_(True))
        )
        if signal is None:
            return False
        is_assigned = signal.psychologist_id == psychologist.id
        is_school_signal = (
            signal.psychologist_id is None
            and psychologist.school is not None
            and signal.student.school == psychologist.school
        )
        if not (is_assigned or is_school_signal):
            return False
        if is_school_signal:
            signal.psychologist_id = psychologist.id
        signal.status = status
        await session.commit()
        return True


async def create_announcement(teacher_id: int, class_name: str, message: str) -> None:
    await ensure_database()
    async with async_session() as session:
        announcement = Announcement(
            teacher_id=teacher_id, class_name=class_name, text=message
        )
        session.add(announcement)
        await session.commit()


async def class_students(teacher: User) -> list[User]:
    await ensure_database()
    classes = [value.strip() for value in (teacher.classes or "").split(",") if value.strip()]
    if not classes or not teacher.school:
        return []
    async with async_session() as session:
        query = select(User).where(User.role == "student")
        query = query.where(
            User.class_name.in_(classes),
            User.school == teacher.school,
        )
        return list(await session.scalars(query.order_by(User.class_name, User.name)))


async def students_for_class(class_name: str, school: str | None = None) -> list[User]:
    await ensure_database()
    if not school:
        return []
    async with async_session() as session:
        query = select(User).where(
            User.role == "student",
            User.class_name == class_name,
        )
        query = query.where(User.school == school)
        return list(await session.scalars(query))


async def parent_children(parent: User) -> list[User]:
    await ensure_database()
    async with async_session() as session:
        links = await session.scalars(
            select(ParentStudentLink)
            .options(selectinload(ParentStudentLink.student))
            .where(
                ParentStudentLink.parent_id == parent.id,
                ParentStudentLink.status == LinkStatus.APPROVED,
            )
            .order_by(ParentStudentLink.created_at)
        )
        return [link.student for link in links]


async def parent_summary(
    parent: User, student_id: int | None = None
) -> tuple[User | None, int, int, list[Deadline]]:
    await ensure_database()
    async with async_session() as session:
        child_query = (
            select(User)
            .join(ParentStudentLink, ParentStudentLink.student_id == User.id)
            .where(
                ParentStudentLink.parent_id == parent.id,
                ParentStudentLink.status == LinkStatus.APPROVED,
            )
            .order_by(User.name)
        )
        if student_id is not None:
            child_query = child_query.where(User.id == student_id)
        child = await session.scalar(child_query.limit(1))
        if child is None:
            return None, 0, 0, []
        week_ago = datetime.now() - timedelta(days=7)
        completed = await session.scalar(
            select(func.count(Homework.id))
            .outerjoin(
                HomeworkCompletion,
                and_(
                    HomeworkCompletion.homework_id == Homework.id,
                    HomeworkCompletion.student_id == child.id,
                ),
            )
            .where(
                or_(
                    and_(
                        Homework.user_id == child.id,
                        Homework.status == "done",
                        Homework.completed_at >= week_ago,
                    ),
                    and_(
                        Homework.class_name == child.class_name,
                        Homework.created_by.in_(
                            select(User.id).where(
                                User.role == "teacher", User.school == child.school
                            )
                        ),
                        HomeworkCompletion.id.is_not(None),
                        HomeworkCompletion.completed_at >= week_ago,
                    ),
                ),
            )
        )
        overdue = await session.scalar(
            select(func.count(Homework.id)).where(
                Homework.deadline < datetime.now(),
                Homework.status == "active",
                or_(
                    and_(
                        Homework.user_id == child.id,
                        ~select(HomeworkCompletion.id)
                        .where(
                            HomeworkCompletion.homework_id == Homework.id,
                            HomeworkCompletion.student_id == child.id,
                        )
                        .exists(),
                    ),
                    and_(
                        Homework.class_name == child.class_name,
                        Homework.created_by.in_(
                            select(User.id).where(
                                User.role == "teacher", User.school == child.school
                            )
                        ),
                        ~select(HomeworkCompletion.id)
                        .where(
                            HomeworkCompletion.homework_id == Homework.id,
                            HomeworkCompletion.student_id == child.id,
                        )
                        .exists(),
                    ),
                ),
            )
        )
        deadlines = list(
            await session.scalars(
                select(Deadline)
                .where(
                    Deadline.date >= datetime.now(),
                    or_(
                        Deadline.user_id == child.id,
                        and_(
                            Deadline.class_name == child.class_name,
                            Deadline.created_by.in_(
                                select(User.id).where(
                                    User.role == "teacher",
                                    User.school == child.school,
                                )
                            ),
                        ),
                    ),
                )
                .order_by(Deadline.date)
                .limit(5)
            )
        )
        return child, completed or 0, overdue or 0, deadlines


async def unlock_reward(user: User, item_key: str, cost: int) -> bool:
    await ensure_database()
    async with async_session() as session:
        existing = await session.scalar(
            select(Reward.id).where(
                Reward.user_id == user.id, Reward.item_key == item_key
            )
        )
        user_row = await session.get(User, user.id)
        if existing is not None or user_row is None or user_row.points < cost:
            return False
        user_row.points -= cost
        session.add(Reward(user_id=user.id, item_key=item_key))
        await session.commit()
        return True


async def create_user_schedule_for_class(
    teacher: User, class_name: str, day: int, lesson_time: time, subject: str, room: str | None
) -> ScheduleItem:
    return await create_schedule_item(
        owner_id=teacher.id,
        creator_id=teacher.id,
        day_of_week=day,
        lesson_time=lesson_time,
        subject=subject,
        room=room,
        class_name=class_name,
    )


async def today_counts_for_teacher(teacher: User) -> tuple[int, int]:
    await ensure_database()
    async with async_session() as session:
        classes = [value.strip() for value in (teacher.classes or "").split(",") if value.strip()]
        if not classes:
            return 0, 0
        active_ids = select(Homework.id).where(
            Homework.created_by == teacher.id,
            Homework.class_name.in_(classes),
            Homework.status == "active",
        )
        assigned = await session.scalar(
            select(func.count(Homework.id)).where(Homework.id.in_(active_ids))
        )
        completed = await session.scalar(
            select(func.count(HomeworkCompletion.id)).where(
                HomeworkCompletion.homework_id.in_(active_ids)
            )
        )
        return assigned or 0, completed or 0


async def teacher_completion_report(teacher: User) -> list[tuple[str, int, int]]:
    await ensure_database()
    students = await class_students(teacher)
    classes = [value.strip() for value in (teacher.classes or "").split(",") if value.strip()]
    if not students or not classes:
        return []
    async with async_session() as session:
        assignments = list(
            await session.scalars(
                select(Homework).where(
                    Homework.created_by == teacher.id,
                    Homework.class_name.in_(classes),
                    Homework.status == "active",
                )
            )
        )
        result = []
        for student in students:
            eligible = [
                item for item in assignments if item.class_name == student.class_name
            ]
            if not eligible:
                continue
            completed = await session.scalar(
                select(func.count(HomeworkCompletion.id)).where(
                    HomeworkCompletion.student_id == student.id,
                    HomeworkCompletion.homework_id.in_(
                        [item.id for item in eligible]
                    ),
                )
            )
            result.append(
                (
                    student.name or f"Ученик #{student.id}",
                    completed or 0,
                    len(eligible),
                )
            )
        return result


async def reminder_already_delivered(user_id: int, delivery_key: str) -> bool:
    await ensure_database()
    async with async_session() as session:
        return (
            await session.scalar(
                select(ReminderDelivery.id).where(
                    ReminderDelivery.user_id == user_id,
                    ReminderDelivery.delivery_key == delivery_key,
                )
            )
        ) is not None


async def record_reminder_delivery(user_id: int, delivery_key: str) -> None:
    await ensure_database()
    async with async_session() as session:
        session.add(
            ReminderDelivery(user_id=user_id, delivery_key=delivery_key)
        )
        await session.commit()


async def reminder_users() -> list[User]:
    await ensure_database()
    async with async_session() as session:
        return list(
            await session.scalars(
                select(User).where(
                    User.reminders_enabled.is_(True),
                    User.role.in_(("student", "parent")),
                )
            )
        )


async def mood_was_logged_today(user_id: int) -> bool:
    await ensure_database()
    async with async_session() as session:
        return (
            await session.scalar(
                select(MoodEntry.id).where(
                    MoodEntry.user_id == user_id,
                    MoodEntry.date == date.today(),
                )
            )
        ) is not None


async def deadlines_for_parent(parent: User) -> list[Deadline]:
    await ensure_database()
    async with async_session() as session:
        children_query = (
            select(User)
            .join(ParentStudentLink, ParentStudentLink.student_id == User.id)
            .where(
                ParentStudentLink.parent_id == parent.id,
                ParentStudentLink.status == LinkStatus.APPROVED,
            )
        )
        children = list(await session.scalars(children_query))
        if not children:
            return []
        conditions = []
        for child in children:
            conditions.append(Deadline.user_id == child.id)
            if child.class_name and child.school:
                conditions.append(
                    and_(
                        Deadline.class_name == child.class_name,
                        Deadline.created_by.in_(
                            select(User.id).where(
                                User.role == "teacher", User.school == child.school
                            )
                        ),
                    )
                )
        return list(
            await session.scalars(
                select(Deadline)
                .where(Deadline.date >= datetime.now(), or_(*conditions))
                .order_by(Deadline.date)
            )
        )
