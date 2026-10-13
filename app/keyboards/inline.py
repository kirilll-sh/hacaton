from __future__ import annotations

from maxapi.types import ButtonsPayload, CallbackButton
from maxapi.types.attachments.buttons.callback_button import CallbackButton as MaxCallbackButton

ButtonRows = list[list[MaxCallbackButton]]


def button(text: str, payload: str) -> MaxCallbackButton:
    return CallbackButton(text=text, payload=payload)


def keyboard(*rows: tuple[tuple[str, str], ...]) -> ButtonsPayload:
    return ButtonsPayload(
        buttons=[
            [button(text, payload) for text, payload in row]
            for row in rows
        ]
    )


def menu_keyboard(role: str) -> ButtonsPayload:
    menus = {
        "student": (
            (("🏫 Войти в класс", "student:join_class"),),
            (("📚 Расписание", "student:schedule"), ("📝 Домашка", "student:homework")),
            (("⏰ Дедлайны", "student:deadlines"), ("🧘 Антистресс", "student:stress")),
            (("🎮 Профиль и очки", "student:profile"), ("⚙️ Настройки", "student:settings")),
            (("📢 Объявления класса", "student:announcements"),),
        ),
        "teacher": (
            (("➕ Создать класс", "teacher:create_class"), ("🏫 Мои классы", "teacher:classes")),
            (("📝 Домашка", "teacher:homework"), ("🗓 Расписание", "teacher:schedule")),
            (("⏰ Контрольные", "teacher:deadline"), ("📊 Статистика", "teacher:stats")),
            (("📢 Объявление", "teacher:announce"),),
            (("⚙️ Настройки", "teacher:settings"),),
        ),
        "parent": (
            (("👨‍👩‍👧 Синхронизироваться с ребёнком", "parent:sync"),),
            (("📊 Учебная сводка", "parent:summary"), ("🔔 Уведомления", "parent:notifications")),
            (("💡 Советы", "parent:tips"), ("⚙️ Настройки", "parent:settings")),
        ),
        "psychologist": (
            (("📥 Сигналы", "psychologist:signals"), ("📊 Статистика", "psychologist:stats")),
            (("⚙️ Настройки", "psychologist:settings"),),
        ),
    }
    return keyboard(*menus.get(role, menus["student"]))


def registration_roles_keyboard() -> ButtonsPayload:
    return keyboard(
        (("🎓 Ученик", "onboard:role:student"),),
        (("🧑‍🏫 Учитель", "onboard:role:teacher"),),
        (("🧠 Психолог", "onboard:role:psychologist"),),
        (("👨‍👩‍👧 Родитель", "onboard:role:parent"),),
    )


def grade_keyboard() -> ButtonsPayload:
    return keyboard(
        tuple((f"{grade} класс", f"onboard:grade:{grade}") for grade in range(7, 12))
    )


def back_menu(role: str) -> ButtonsPayload:
    return keyboard((("⬅️ В меню", f"menu:{role}"),))
