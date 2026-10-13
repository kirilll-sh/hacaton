from __future__ import annotations

from datetime import datetime

import pytest
from app.keyboards.inline import keyboard, menu_keyboard
from app.main import _day_number, _parse_datetime
from app.services.mvp import analyze_stress


def test_stress_analyzer_scores_multiple_themes() -> None:
    score, themes = analyze_stress("Я очень устал, всё бесит и становится страшно")

    assert score == 4
    assert "усталость" in themes
    assert "тревога" in themes


def test_stress_analyzer_flags_urgent_words() -> None:
    score, themes = analyze_stress("Я хочу умереть")

    assert score == 5
    assert "требуется срочная поддержка" in themes


def test_student_menu_uses_inline_callback_buttons() -> None:
    buttons = menu_keyboard("student")

    assert any(
        item.payload == "student:homework"
        for row in buttons.buttons
        for item in row
    )


def test_callback_payload_is_attached_to_inline_button() -> None:
    buttons = keyboard((("Назад", "menu:student"),))

    assert buttons.buttons[0][0].text == "Назад"
    assert buttons.buttons[0][0].payload == "menu:student"


def test_date_and_weekday_inputs_are_parsed() -> None:
    assert _parse_datetime("25.10.2026 18:30") == datetime(2026, 10, 25, 18, 30)
    assert _parse_datetime("25.10.2026") == datetime(2026, 10, 25, 23, 59)
    assert _day_number("пн") == 1


def test_invalid_schedule_inputs_are_rejected() -> None:
    with pytest.raises(ValueError):
        _parse_datetime("завтра")

    with pytest.raises(ValueError):
        _day_number("8")
