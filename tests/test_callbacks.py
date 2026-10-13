from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from maxapi import Bot
from maxapi.enums.chat_type import ChatType
from maxapi.types import CallbackButton, Message, MessageCallback, Recipient
from maxapi.types.callback import Callback
from maxapi.types.users import User as MaxUser

from app import main
from app.db.models import User
from app.keyboards.inline import ButtonsPayload, menu_keyboard


def callback_event(
    payload: str, bot: Bot
) -> MessageCallback:
    event = MessageCallback(
        timestamp=1,
        message=Message(
            recipient=Recipient(chat_id=10, chat_type=ChatType.DIALOG),
            timestamp=1,
        ),
        callback=Callback(
            timestamp=1,
            callback_id="callback-id",
            payload=payload,
            user=MaxUser(user_id=42, first_name="Test", is_bot=False),
        ),
    )
    event.bot = bot
    return event


@pytest.mark.parametrize(
    ("role", "expected_text"),
    [
        ("student", "В каком ты классе?"),
        ("teacher", "В какой школе работаешь?"),
        ("parent", "Введи числовой MAX ID ребёнка."),
        ("psychologist", "В какой школе работаешь?"),
    ],
)
def test_role_selection_callback_reaches_onboarding(
    monkeypatch: pytest.MonkeyPatch, role: str, expected_text: str
) -> None:
    user = User(max_user_id="42", name="Иванов Иван Иванович", role="unselected")
    sent: list[tuple[int | None, str, ButtonsPayload | None, int | None]] = []
    acknowledgements: list[bool] = []
    bot = Bot(token="test-token")

    async def ack(callback_id: str, **kwargs: object) -> None:
        assert callback_id == "callback-id"
        assert kwargs == {"notification": "Запрос принят."}
        acknowledgements.append(True)

    async def find_user(max_user_id: str) -> User:
        assert max_user_id == "42"
        return user

    async def update_profile(max_user_id: str, **values: object) -> User:
        assert max_user_id == "42"
        for key, value in values.items():
            setattr(user, key, value)
        return user

    async def set_state(user: User, step: str | None, **data: object) -> None:
        assert user.max_user_id == "42"
        assert step is not None
        assert not data

    async def send(
        chat_id: int | None,
        text: str,
        buttons: ButtonsPayload | None = None,
        user_id: int | None = None,
    ) -> None:
        sent.append((chat_id, text, buttons, user_id))

    monkeypatch.setattr(main.mvp, "user_by_max_id", find_user)
    monkeypatch.setattr(main.mvp, "update_profile", update_profile)
    monkeypatch.setattr(main, "_set_state", set_state)
    monkeypatch.setattr(main, "_send", send)
    monkeypatch.setattr(bot, "send_callback", ack)

    event = callback_event(f"onboard:role:{role}", bot)

    asyncio.run(main._handle_callback(event))

    assert acknowledgements == [True]
    assert user.role == role
    assert len(sent) == 1
    assert expected_text in sent[0][1]
    if role == "student":
        buttons = sent[0][2]
        assert buttons is not None
        payloads = []
        for row in buttons.buttons:
            for button in row:
                assert isinstance(button, CallbackButton)
                payloads.append(button.payload)
        assert set(payloads) == {
            f"onboard:grade:{grade}" for grade in range(7, 12)
        }


def test_staff_role_requests_full_name_when_it_was_not_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(max_user_id=42, role="unselected")
    sent: list[str] = []

    async def find_user(max_user_id: str) -> User:
        return user

    async def update_profile(max_user_id: str, **values: object) -> User:
        for key, value in values.items():
            setattr(user, key, value)
        return user

    async def set_state(user: User, step: str | None, **data: object) -> None:
        main._states[str(user.max_user_id)] = {"step": step, **data}

    async def send(
        chat_id: int | None,
        text: str,
        buttons: ButtonsPayload | None = None,
        user_id: int | None = None,
    ) -> None:
        sent.append(text)

    async def ack(callback_id: str, **kwargs: object) -> None:
        assert kwargs == {"notification": "Запрос принят."}

    monkeypatch.setattr(main.mvp, "user_by_max_id", find_user)
    monkeypatch.setattr(main.mvp, "update_profile", update_profile)
    monkeypatch.setattr(main, "_set_state", set_state)
    monkeypatch.setattr(main, "_send", send)
    bot = Bot(token="test-token")
    monkeypatch.setattr(bot, "send_callback", ack)

    asyncio.run(main._handle_callback(callback_event("onboard:role:teacher", bot)))

    assert sent == ["Для заявки укажи ФИО."]
    assert main._get_state("42") == {"step": "onboarding_staff_name"}
    main._states.pop("42", None)


def test_pending_staff_start_requests_missing_full_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(
        max_user_id=42,
        role="teacher",
        status="pending",
    )
    sent: list[str] = []

    async def find_user(max_user_id: str) -> User:
        assert max_user_id == "42"
        return user

    async def send(
        chat_id: int | None,
        text: str,
        buttons: ButtonsPayload | None = None,
        user_id: int | None = None,
    ) -> None:
        sent.append(text)

    monkeypatch.setattr(main.mvp, "user_by_max_id", find_user)
    monkeypatch.setattr(main, "_send", send)
    event = SimpleNamespace(
        from_user=SimpleNamespace(user_id=42),
        chat=SimpleNamespace(chat_id=10),
        message=SimpleNamespace(body=SimpleNamespace(text="/start")),
    )

    asyncio.run(main._handle_message_created(event))

    assert sent == ["Для заявки сотрудника не сохранено ФИО. Пожалуйста, введите ФИО."]
    assert main._get_state("42") == {"step": "onboarding_staff_name_pending"}
    main._states.pop("42", None)


def test_staff_application_confirmation_includes_max_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(max_user_id=42, role="teacher")
    sent: list[str] = []

    async def update_profile(max_user_id: str, **values: object) -> User:
        assert max_user_id == "42"
        assert values == {"school": "Гимназия"}
        return user

    async def set_state(user: User, step: str | None, **data: object) -> None:
        assert step is None

    async def send(
        chat_id: int | None,
        text: str,
        buttons: ButtonsPayload | None = None,
        user_id: int | None = None,
    ) -> None:
        sent.append(text)

    monkeypatch.setattr(main.mvp, "update_profile", update_profile)
    monkeypatch.setattr(main, "_set_state", set_state)
    monkeypatch.setattr(main, "_send", send)

    handled = asyncio.run(
        main._handle_input_step(
            chat_id=10,
            max_id=42,
            user=user,
            step="onboarding_school",
            text="Гимназия",
            state={},
        )
    )

    assert handled is True
    assert sent == [
        "Заявка отправлена администратору. До подтверждения доступ к кабинету "
        "сотрудника закрыт.\nВаш MAX ID: 42."
    ]


@pytest.mark.parametrize("role", ["student", "teacher", "parent", "psychologist"])
def test_all_role_menus_serialize_callback_buttons(role: str) -> None:
    attachment = menu_keyboard(role).pack().model_dump(mode="json")

    assert attachment["type"] == "inline_keyboard"
    buttons = attachment["payload"]["buttons"]
    assert buttons
    assert all(
        button["type"] == "callback" and button["payload"]
        for row in buttons
        for button in row
    )


def test_teacher_class_registration_step_is_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(max_user_id="42", role="teacher")
    saved: dict[str, object] = {}
    menu_calls: list[tuple[int | None, User, int | None]] = []

    async def update_profile(max_user_id: str, **values: object) -> User:
        assert max_user_id == "42"
        saved.update(values)
        return user

    async def set_state(user: User, step: str | None, **data: object) -> None:
        assert user.role == "teacher"
        assert step is None
        assert not data

    async def find_user(max_user_id: str) -> User:
        assert max_user_id == "42"
        return user

    async def show_menu(
        chat_id: int | None, user: User, max_id: int | None
    ) -> None:
        menu_calls.append((chat_id, user, max_id))

    monkeypatch.setattr(main.mvp, "update_profile", update_profile)
    monkeypatch.setattr(main.mvp, "user_by_max_id", find_user)
    monkeypatch.setattr(main, "_set_state", set_state)
    monkeypatch.setattr(main, "_menu", show_menu)

    handled = asyncio.run(
        main._handle_input_step(
            chat_id=10,
            max_id=42,
            user=user,
            step="onboarding_classes",
            text="8А, 9Б",
            state={},
        )
    )

    assert handled is True
    assert saved["classes"] == "8А,9Б"
    assert len(menu_calls) == 1


def test_parent_notifications_button_opens_notification_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(max_user_id="42", role="parent", reminders_enabled=True)
    sent: list[tuple[int | None, str, ButtonsPayload | None, int | None]] = []
    bot = Bot(token="test-token")

    async def find_user(max_user_id: str) -> User:
        assert max_user_id == "42"
        return user

    async def send(
        chat_id: int | None,
        text: str,
        buttons: ButtonsPayload | None = None,
        user_id: int | None = None,
    ) -> None:
        sent.append((chat_id, text, buttons, user_id))

    async def ack(callback_id: str, **kwargs: object) -> None:
        assert callback_id == "callback-id"
        assert kwargs == {"notification": "Запрос принят."}
        return None

    monkeypatch.setattr(main.mvp, "user_by_max_id", find_user)
    monkeypatch.setattr(main, "_send", send)
    monkeypatch.setattr(bot, "send_callback", ack)

    event = callback_event("parent:notifications", bot)

    asyncio.run(main._handle_callback(event))

    assert "🔔 Уведомления включены." in sent[0][1]
    buttons = sent[0][2]
    assert buttons is not None
    button = buttons.buttons[0][0]
    assert isinstance(button, CallbackButton)
    assert button.payload == "settings:reminders"
