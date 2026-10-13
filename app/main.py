from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from maxapi import Bot, Dispatcher
from maxapi.enums.attachment import AttachmentType
from maxapi.types import Attachment

from app import config
from app.db import crud
from app.db.database import close_database, create_db_and_tables
from app.db.models import RequestStatus, User, UserRole
from app.keyboards.inline import (
    grade_keyboard,
    keyboard,
    menu_keyboard,
    registration_roles_keyboard,
)
from app.services import mvp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

TOKEN = config.BOT_TOKEN
bot = Bot(token=TOKEN or "missing-token")
dp = Dispatcher()
_states: dict[str, dict[str, Any]] = {}

ROLE_LABELS = {
    "student": "ученика",
    "teacher": "учителя",
    "psychologist": "психолога",
    "parent": "родителя",
}


def _missing_full_name(user: User) -> bool:
    return not user.full_name.strip() or user.full_name.strip() == "Пользователь"


def _max_user_id(event: Any) -> str | None:
    users = (
        getattr(event, "from_user", None),
        getattr(getattr(event, "callback", None), "user", None),
        getattr(getattr(event, "message", None), "sender", None),
    )
    for user in users:
        user_id = getattr(user, "user_id", None)
        if user_id is not None:
            return str(user_id)
    return None


def _chat_id(event: Any) -> int | None:
    chat = getattr(event, "chat", None)
    chat_id = getattr(chat, "chat_id", None)
    if chat_id is not None:
        return chat_id
    recipient = getattr(getattr(event, "message", None), "recipient", None)
    return getattr(recipient, "chat_id", None)


async def _send(
    chat_id: int | None,
    text: str,
    buttons: Any | None = None,
    user_id: int | None = None,
) -> None:
    await bot.send_message(
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        attachments=[
            Attachment(type=AttachmentType.INLINE_KEYBOARD, payload=buttons)
        ]
        if buttons is not None
        else None,
    )


async def _set_state(user: User, step: str | None, **data: Any) -> None:
    key = str(user.max_user_id)
    if step is None:
        _states.pop(key, None)
    else:
        _states[key] = {"step": step, **data}


def _get_state(max_id: str) -> dict[str, Any] | None:
    return _states.get(str(max_id))


def _role_value(role: UserRole | str) -> str:
    return role.value if isinstance(role, UserRole) else str(role)


async def _menu(chat_id: int | None, user: User, max_id: int | None) -> None:
    role = _role_value(user.role)
    await _send(
        chat_id,
        f"Кабинет {ROLE_LABELS.get(role, 'пользователя')}. Выберите действие:",
        menu_keyboard(role),
    )


async def _send_link_request(link_id: int, parent: User, student: User) -> None:
    buttons = keyboard(
        (
            ("✅ Подтвердить", f"link_approve:{link_id}"),
            ("❌ Отклонить", f"link_reject:{link_id}"),
        )
    )
    await _send(
        None,
        f"🔔 Пользователь {parent.name} хочет привязать ваш аккаунт как родитель. "
        "Вы подтверждаете?",
        buttons,
        user_id=int(student.max_user_id),
    )


async def _handle_input_step(
    chat_id: int | None,
    max_id: int,
    user: User,
    step: str,
    text: str,
    state: dict[str, Any],
) -> bool:
    value = text.strip()
    if not value:
        await _send(chat_id, "Пожалуйста, отправьте непустое значение.")
        return True

    if step == "register_name":
        if len(value) > 255:
            await _send(chat_id, "Имя слишком длинное. Введите ФИО (до 255 символов).")
            return True
        await crud.save_unselected_user(str(max_id), value)
        await _set_state(user, None)
        await _send(
            chat_id,
            "Выберите роль. После регистрации ее нельзя будет изменить:",
            registration_roles_keyboard(),
        )
        return True

    if step in {"onboarding_staff_name", "onboarding_staff_name_pending"}:
        if len(value) > 255:
            await _send(chat_id, "ФИО слишком длинное. Введите ФИО (до 255 символов).")
            return True
        await mvp.update_profile(str(max_id), full_name=value)
        if step == "onboarding_staff_name_pending":
            await _set_state(user, None)
            await _send(
                chat_id,
                "ФИО сохранено. Заявка уже отправлена администратору. "
                f"Ваш MAX ID: {max_id}. Дождитесь подтверждения.",
            )
        else:
            await _set_state(user, "onboarding_school")
            await _send(chat_id, "В какой школе работаешь?")
        return True

    if step == "onboarding_classes":
        classes = ",".join(part.strip() for part in value.split(",") if part.strip())
        if len(classes) > 255:
            await _send(chat_id, "Слишком длинное значение. Укажите класс, например 9А.")
            return True
        await mvp.update_profile(str(max_id), classes=classes, class_name=classes)
        await _set_state(user, None)
        await _menu(chat_id, user, max_id)
        return True

    if step == "onboarding_school":
        if len(value) > 255:
            await _send(chat_id, "Название школы слишком длинное.")
            return True
        await mvp.update_profile(str(max_id), school=value)
        await _set_state(user, None)
        await _send(
            chat_id,
            "Заявка отправлена администратору. До подтверждения доступ к кабинету "
            f"сотрудника закрыт.\nВаш MAX ID: {max_id}.",
        )
        return True

    if step == "onboarding_parent_child":
        candidates = await crud.student_candidates(value)
        if not candidates:
            await _send(
                chat_id,
                "Ученик не найден. Проверьте ФИО или MAX ID из профиля ученика и попробуйте еще раз.",
            )
            return True
        if len(candidates) > 1:
            options = tuple(
                (
                    f"{item.name} — {item.class_name or 'класс не указан'}, "
                    f"MAX ID {item.max_user_id}",
                    f"parent:select_child:{item.id}",
                )
                for item in candidates
            )
            await _send(
                chat_id,
                "Найдено несколько учеников. Выберите нужного:",
                keyboard(*((option,) for option in options)),
            )
            return True
        await _request_link(chat_id, user, candidates[0])
        await _set_state(user, None)
        return True

    if step == "teacher_class_name":
        if len(value) > 64:
            await _send(chat_id, "Название класса должно быть не длиннее 64 символов.")
            return True
        await _set_state(user, "teacher_class_login", class_name=value)
        await _send(chat_id, "Придумайте логин для входа в класс.")
        return True

    if step == "teacher_class_login":
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", value):
            await _send(
                chat_id,
                "Логин должен содержать 3–64 латинских буквы, цифры, дефис или подчеркивание.",
            )
            return True
        await _set_state(
            user,
            "teacher_class_password",
            class_name=state["class_name"],
            login=value.lower(),
        )
        await _send(chat_id, "Задайте пароль для входа (не менее 8 символов).")
        return True

    if step == "teacher_class_password":
        if len(value) < 8 or len(value.encode("utf-8")) > 256:
            await _send(chat_id, "Пароль должен содержать от 8 до 256 байт.")
            return True
        try:
            school_class = await crud.create_school_class(
                user.id,
                state["class_name"],
                state["login"],
                value,
            )
        except (ValueError, PermissionError, RuntimeError) as error:
            await _set_state(user, None)
            await _send(chat_id, str(error))
            return True
        await _set_state(user, None)
        await _send(
            chat_id,
            f"Класс {school_class.name} создан.\n"
            f"Логин: {school_class.login}\n"
            f"Пароль: {value}\n"
            "Сохраните его и передайте ученикам.",
        )
        return True

    if step == "student_join_login":
        if len(value) > 64:
            await _send(chat_id, "Логин не найден. Проверьте его и попробуйте снова.")
            return True
        await _set_state(user, "student_join_password", login=value)
        await _send(chat_id, "Введите пароль класса.")
        return True

    if step == "student_join_password":
        try:
            message = await crud.join_school_class(
                user.id,
                state["login"],
                value,
            )
        except ValueError as error:
            await _set_state(user, None)
            await _send(chat_id, str(error))
            return True
        except PermissionError as error:
            await _set_state(user, None)
            await _send(chat_id, str(error))
            return True
        await _set_state(user, None)
        await _send(chat_id, message)
        return True

    return False


async def _request_link(chat_id: int | None, parent: User, student: User) -> None:
    link, created = await crud.request_parent_student_link(parent.id, student.id)
    if not created:
        if link.status == "approved":
            await _send(chat_id, f"{student.name} уже привязан к вашему кабинету.")
        else:
            await _send(chat_id, f"Запрос ученику {student.name} уже ожидает подтверждения.")
        return
    await _send(chat_id, f"Запрос на связь отправлен ученику {student.name}. Ожидайте подтверждения.")
    await _send_link_request(link.id, parent, student)


def _parse_datetime(value: str) -> datetime:
    for pattern in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(value.strip(), pattern)
            if pattern == "%d.%m.%Y":
                return parsed.replace(hour=23, minute=59)
            return parsed
        except ValueError:
            continue
    raise ValueError("Дата должна быть в формате ДД.ММ.ГГГГ [ЧЧ:ММ].")


def _day_number(value: str) -> int:
    names = {
        "пн": 1,
        "понедельник": 1,
        "вт": 2,
        "вторник": 2,
        "ср": 3,
        "среда": 3,
        "чт": 4,
        "четверг": 4,
        "пт": 5,
        "пятница": 5,
        "сб": 6,
        "суббота": 6,
        "вс": 7,
        "воскресенье": 7,
    }
    try:
        return names[value.strip().casefold()]
    except KeyError as error:
        raise ValueError("Укажите день недели (пн–вс).") from error


async def _handle_message_created(event: Any) -> None:
    max_id = _max_user_id(event)
    chat_id = _chat_id(event)
    text = getattr(getattr(getattr(event, "message", None), "body", None), "text", None)
    if max_id is None or chat_id is None or not text:
        return
    text = text.strip()

    try:
        user = await mvp.user_by_max_id(max_id)
        if text.split(maxsplit=1)[0].split("@", 1)[0] == "/start":
            if user is None:
                await _set_state(
                    User(max_user_id=max_id, role=UserRole.UNSELECTED),
                    "register_name",
                )
                await _send(chat_id, "Добро пожаловать! Введите ваше ФИО.")
                return
            if user.status == RequestStatus.PENDING:
                if (
                    user.role in {UserRole.TEACHER, UserRole.PSYCHOLOGIST}
                    and _missing_full_name(user)
                ):
                    await _set_state(user, "onboarding_staff_name_pending")
                    await _send(
                        chat_id,
                        "Для заявки сотрудника не сохранено ФИО. "
                        "Пожалуйста, введите ФИО.",
                    )
                    return
                await _send(
                    chat_id,
                    "Ваша заявка на роль сотрудника школы находится на проверке у администратора. Ожидайте уведомления.",
                )
                return
            if user.status == RequestStatus.REJECTED:
                await _send(chat_id, "Заявка отклонена. Обратитесь к администратору школы.")
                return
            if user.role == UserRole.UNSELECTED:
                await _send(
                    chat_id,
                    "Выберите роль. После регистрации ее нельзя будет изменить:",
                    registration_roles_keyboard(),
                )
                return
            await _menu(chat_id, user, int(max_id))
            return

        state = _get_state(max_id)
        if state is not None:
            if user is None:
                user = User(max_user_id=max_id, role=UserRole.UNSELECTED)
            pending_staff_onboarding = (
                state["step"]
                in {
                    "onboarding_school",
                    "onboarding_staff_name",
                    "onboarding_staff_name_pending",
                }
                and user.role in {UserRole.TEACHER, UserRole.PSYCHOLOGIST}
                and user.status == RequestStatus.PENDING
            )
            if user.status != RequestStatus.APPROVED and not pending_staff_onboarding:
                await _send(
                    chat_id,
                    "Доступ к этому действию закрыт. Отправьте /start, чтобы проверить статус.",
                )
                return
            handled = await _handle_input_step(
                chat_id,
                int(max_id),
                user,
                state["step"],
                text,
                state,
            )
            if handled:
                return

        if user is None:
            await _send(chat_id, "Для начала работы отправьте /start.")
        elif user.status != RequestStatus.APPROVED:
            await _send(chat_id, "Доступ к кабинету пока не подтвержден. Отправьте /start для проверки статуса.")
        else:
            await _send(chat_id, "Используйте кнопки меню или отправьте /start.")
    except Exception:
        logger.exception("Failed to handle MAX message from user %s", max_id)
        await _send(chat_id, "Не удалось обработать запрос. Попробуйте еще раз позже.")


async def _handle_callback(event: Any) -> None:
    callback = getattr(event, "callback", None)
    payload = getattr(callback, "payload", None)
    max_id = _max_user_id(event)
    chat_id = _chat_id(event)
    callback_bot = getattr(event, "bot", None) or bot
    callback_id = getattr(callback, "callback_id", None)
    message = getattr(event, "message", None)
    can_edit_callback_message = (
        callback_id is not None
        and getattr(message, "body", None) is not None
        and callable(getattr(event, "edit", None))
    )
    is_link_decision = bool(
        payload
        and (payload.startswith("link_approve:") or payload.startswith("link_reject:"))
    )
    callback_acknowledged = False

    async def acknowledge_callback() -> None:
        nonlocal callback_acknowledged
        if callback_id and not callback_acknowledged:
            callback_acknowledged = True
            try:
                await callback_bot.send_callback(
                    callback_id=callback_id,
                    notification="Запрос принят.",
                )
            except Exception:
                logger.exception("Could not acknowledge MAX callback %s", callback_id)

    if not (is_link_decision and can_edit_callback_message):
        await acknowledge_callback()
    if not payload or max_id is None:
        await acknowledge_callback()
        return

    try:
        user = await mvp.user_by_max_id(max_id)
        if payload.startswith("onboard:role:"):
            role = payload.rsplit(":", 1)[-1]
            if role not in ROLE_LABELS:
                await _send(chat_id, "Неизвестная роль. Отправьте /start, чтобы выбрать роль.")
                return
            if user is not None and user.role != UserRole.UNSELECTED:
                if user.status == RequestStatus.APPROVED:
                    await _menu(chat_id, user, int(max_id))
                elif user.status == RequestStatus.PENDING:
                    await _send(chat_id, "Ваша заявка еще рассматривается администратором.")
                else:
                    await _send(chat_id, "Заявка отклонена. Обратитесь к администратору школы.")
                return
            employee = role in {"teacher", "psychologist"}
            if user is None:
                user = await mvp.update_profile(
                    max_id,
                    role=role,
                    status="pending" if employee else "approved",
                )
            else:
                user = await mvp.update_profile(
                    max_id,
                    role=role,
                    status="pending" if employee else "approved",
                )
            if role == "student":
                await _set_state(user, "onboarding_classes")
                await _send(
                    chat_id,
                    "В каком ты классе? Укажи класс, например 9А. "
                    "Для вступления в класс понадобится логин и пароль учителя.",
                    grade_keyboard(),
                )
            elif employee:
                if _missing_full_name(user):
                    await _set_state(user, "onboarding_staff_name")
                    await _send(chat_id, "Для заявки укажи ФИО.")
                else:
                    await _set_state(user, "onboarding_school")
                    await _send(chat_id, "В какой школе работаешь?")
            else:
                await _set_state(user, "onboarding_parent_child")
                await _send(
                    chat_id,
                    "Введи числовой MAX ID ребёнка. Также можно указать точное ФИО "
                    "(MAX ID можно посмотреть в профиле ученика).",
                )
            return

        if payload.startswith("onboard:grade:"):
            if (
                user is None
                or user.role != UserRole.STUDENT
                or user.status != RequestStatus.APPROVED
            ):
                await _send(chat_id, "Выбор класса доступен только зарегистрированному ученику.")
                return
            try:
                grade = int(payload.rsplit(":", 1)[-1])
            except ValueError:
                await _send(chat_id, "Некорректно указан класс.")
                return
            if grade not in range(7, 12):
                await _send(chat_id, "Выберите класс с помощью кнопки.")
                return
            await crud.set_student_grade(user.id, grade)
            await _send(chat_id, f"Выбран {grade} класс. Теперь укажи класс, например 9А.")
            return

        if payload.startswith("link_approve:") or payload.startswith("link_reject:"):
            if user is None or user.role != UserRole.STUDENT or user.status != RequestStatus.APPROVED:
                await acknowledge_callback()
                await _send(chat_id, "Подтверждать запросы может только зарегистрированный ученик.")
                return
            approve = payload.startswith("link_approve:")
            try:
                link_id = int(payload.split(":", 1)[1])
            except ValueError:
                await acknowledge_callback()
                await _send(chat_id, "Некорректный запрос.")
                return
            decision = await crud.decide_parent_student_link(
                link_id, max_id, approve=approve
            )
            if decision is None:
                await acknowledge_callback()
                await _send(chat_id, "Запрос уже обработан или предназначен другому ученику.")
                return
            if approve:
                student_response = (
                    f"Вы подтвердили привязку родителя {decision.parent_name}."
                )
                notice = (
                    f"🎉 Ученик {decision.student_name} подтвердил синхронизацию! "
                    "Теперь вы можете просматривать его учебную сводку."
                )
            else:
                student_response = (
                    f"Вы отклонили запрос родителя {decision.parent_name}."
                )
                notice = f"К сожалению, запрос на синхронизацию с {decision.student_name} был отклонен."
            if can_edit_callback_message:
                try:
                    await event.edit(text=student_response, attachments=[])
                except Exception:
                    logger.exception("Could not edit parent-link callback message")
                    await acknowledge_callback()
                    await _send(chat_id, student_response)
            else:
                await acknowledge_callback()
                await _send(chat_id, student_response)
            try:
                await _send(None, notice, user_id=decision.parent_max_user_id)
            except Exception:
                logger.exception(
                    "Could not notify parent MAX user %s about link decision",
                    decision.parent_max_user_id,
                )
            return

        if user is None:
            await acknowledge_callback()
            await _send(chat_id, "Для начала работы отправьте /start.")
            return
        if payload.startswith("parent:select_child:"):
            if user.role != UserRole.PARENT or user.status != RequestStatus.APPROVED:
                await _send(chat_id, "Эта функция доступна только родителю.")
                return
            student_id = int(payload.rsplit(":", 1)[-1])
            student = await mvp.user_by_id(student_id)
            if student is None:
                await _send(chat_id, "Ученик не найден.")
            else:
                await _request_link(chat_id, user, student)
            return

        if payload.startswith("menu:"):
            if (
                user.status != RequestStatus.APPROVED
                or payload != f"menu:{_role_value(user.role)}"
            ):
                await _send(chat_id, "Доступ к этому кабинету запрещен.")
                return
            await _menu(chat_id, user, int(max_id))
            return

        action_roles = {
            "student:": UserRole.STUDENT,
            "teacher:": UserRole.TEACHER,
            "parent:": UserRole.PARENT,
            "psychologist:": UserRole.PSYCHOLOGIST,
        }
        required_role = next(
            (role for prefix, role in action_roles.items() if payload.startswith(prefix)),
            None,
        )
        if (
            required_role is not None
            and (user.role != required_role or user.status != RequestStatus.APPROVED)
        ):
            await _send(chat_id, "У вас нет доступа к этой функции.")
            return

        if payload == "teacher:create_class":
            await _set_state(user, "teacher_class_name")
            await _send(chat_id, "Введите название класса, например 10Б.")
            return
        if payload == "teacher:classes":
            classes = await crud.classes_for_teacher(user.id)
            if not classes:
                await _send(chat_id, "Пока нет созданных классов.", menu_keyboard("teacher"))
                return
            try:
                listing = "\n".join(
                    f"• {item.name} — логин: {item.login}; "
                    f"пароль: {crud.decrypt_class_password(item.password_encrypted)}."
                    for item in classes
                )
            except RuntimeError:
                logger.exception("Could not decrypt class passwords for teacher %s", user.id)
                await _send(
                    chat_id,
                    "Не удалось расшифровать пароль. Проверьте CLASS_PASSWORD_KEY в настройках.",
                )
                return
            await _send(chat_id, listing, menu_keyboard("teacher"))
            return
        if payload == "student:join_class":
            await _set_state(user, "student_join_login")
            await _send(chat_id, "Введите логин класса.")
            return
        if payload == "parent:sync":
            await _set_state(user, "onboarding_parent_child")
            await _send(
                chat_id,
                "Введи числовой MAX ID ребёнка. Также можно указать точное ФИО "
                "(MAX ID можно посмотреть в профиле ученика).",
            )
            return
        if payload == "student:profile":
            await _send(
                chat_id,
                f"Профиль: {user.name}\nУникальный MAX ID для привязки родителя: {user.max_user_id}\n"
                f"Очки: {user.points}",
            )
            return
        if payload == "parent:summary":
            links = await crud.approved_parent_links(user.id)
            if not links:
                await _send(chat_id, "Пока нет подтвержденных связей с учениками.")
                return
            summaries = []
            for link in links:
                student, completed, overdue, deadlines = await mvp.parent_summary(
                    user, student_id=link.student.id
                )
                if student is None:
                    continue
                summary = (
                    f"• {student.name} ({student.class_name or 'класс не указан'}): "
                    f"выполнено за неделю — {completed}, просрочено — {overdue}."
                )
                if deadlines:
                    summary += "\n  Ближайшие сроки: " + "; ".join(
                        f"{item.title} — {item.date:%d.%m.%Y}" for item in deadlines
                    )
                summaries.append(summary)
            await _send(
                chat_id,
                "Учебная сводка:\n" + "\n".join(summaries)
                if summaries
                else "Для учеников пока нет данных учебной сводки.",
            )
            return
        if payload == "parent:notifications":
            await _send(
                chat_id,
                "🔔 Уведомления включены."
                if user.reminders_enabled
                else "🔕 Уведомления выключены.",
                keyboard((("Изменить настройки", "settings:reminders"),)),
            )
            return
        if payload == "settings:reminders":
            if (
                user.role not in {UserRole.STUDENT, UserRole.PARENT}
                or user.status != RequestStatus.APPROVED
            ):
                await _send(chat_id, "У вас нет доступа к этой функции.")
                return
            enabled = not user.reminders_enabled
            await mvp.update_profile(max_id, reminders_enabled=enabled)
            await _send(
                chat_id,
                "🔔 Уведомления включены." if enabled else "🔕 Уведомления выключены.",
                keyboard((("⬅️ В меню", "menu:parent"),)),
            )
            return
        if payload.startswith("parent:") and payload not in {
            "parent:notifications",
            "parent:tips",
            "parent:settings",
        }:
            await _send(chat_id, "Неизвестное действие родительского кабинета.")
            return
        await _send(chat_id, "Эта функция пока недоступна. Отправьте /start, чтобы открыть меню.")
    except Exception:
        logger.exception("Failed to handle callback %r from user %s", payload, max_id)
        await acknowledge_callback()
        await _send(chat_id, "Не удалось выполнить действие. Попробуйте еще раз.")


@dp.message_created()
async def handle_message_created(event: Any) -> None:
    await _handle_message_created(event)


@dp.message_callback()
async def handle_message_callback(event: Any) -> None:
    await _handle_callback(event)


async def main() -> None:
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN must be set in the environment.")
    await create_db_and_tables()
    logger.info("MAX-бот «Школьный штаб» запущен.")
    try:
        await dp.start_polling(bot)
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
