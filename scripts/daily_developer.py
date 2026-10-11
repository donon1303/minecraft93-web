#!/usr/bin/env python3
"""Create small, reviewable improvements to the Minecraft server landing page."""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "frontend" / "93-server (5).html"
API_URL = "https://api.openai.com/v1/chat/completions"
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
REQUIRED_MARKERS = (
    '<html lang="ru">',
    'id="top"',
    'id="join"',
    'id="about"',
    'id="rules"',
    'id="faq"',
    'id="community"',
    'id="theme"',
    'id="copy"',
    'chupki93.drun.ninja',
)


class PageParser(HTMLParser):
    """Basic parser check: ensures the resulting document is parseable as HTML."""


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result


def request_patches(html: str, count: int) -> list[dict[str, str]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Не задан секрет OPENAI_API_KEY в настройках GitHub Actions.")

    system_prompt = """Ты опытный веб-разработчик, который улучшает существующую русскоязычную страницу Minecraft-сервера.
Верни только корректный JSON-объект вида {\"changes\":[{\"title\":\"краткий заголовок\",\"reason\":\"почему это полезно\",\"old\":\"точный фрагмент исходного HTML\",\"new\":\"новый фрагмент\"}]}.
Каждое изменение должно быть небольшим, полезным, безопасным и независимым от остальных. old должен дословно встречаться в переданном HTML ровно один раз. Выбирай непересекающиеся фрагменты, чтобы их можно было применить последовательно.
Меняй только разметку, CSS или видимые тексты ДО первого тега <script>. Не меняй JavaScript, IP-адрес сервера, ссылки Discord/Telegram, названия версий Minecraft и существующие id секций. Не добавляй скрипты, обработчики событий в HTML, новые внешние зависимости, формы сбора данных или выдуманные факты. Не делай косметические изменения без пользы. Не используй один и тот же фрагмент для нескольких изменений.
Верни столько изменений, сколько запрошено, если можешь придумать действительно полезные изменения. Не создавай пустые или фиктивные изменения. Тексты интерфейса должны быть на русском. Фрагменты old/new должны быть JSON-строками с корректным экранированием."""
    user_prompt = (
        f"Подготовь до {count} отдельных полезных улучшений для этой страницы. "
        "Если качественных изменений меньше, верни только качественные. "
        "Для каждого old используй точную подстроку из HTML, не обрезай её неоднозначно.\n\n"
        "ТЕКУЩИЙ HTML:\n```html\n" + html + "\n```"
    )
    payload = {
        "model": MODEL,
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 10000,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1500]
        raise RuntimeError(f"OpenAI API вернул HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Не удалось подключиться к OpenAI API: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        changes = parsed["changes"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("ИИ вернул ответ в неожиданном формате JSON.") from exc
    if not isinstance(changes, list):
        raise RuntimeError("Поле changes в ответе ИИ должно быть списком.")
    return changes[:count]


def validate_html(
    html: str,
    original_script_count: int,
    original_hrefs: list[str] | None = None,
    original_script_tail: str | None = None,
) -> None:
    if not html.lstrip().lower().startswith("<!doctype html"):
        raise RuntimeError("Проверка остановлена: отсутствует <!DOCTYPE html>.")
    for marker in REQUIRED_MARKERS:
        if marker not in html:
            raise RuntimeError(f"Проверка остановлена: пропал обязательный элемент {marker!r}.")
    if html.lower().count("<script") != original_script_count:
        raise RuntimeError("Проверка остановлена: число JavaScript-блоков изменилось.")
    if original_hrefs is not None:
        hrefs = re.findall(r"\bhref\s*=\s*[\"\']([^\"\']*)", html, re.I)
        if hrefs != original_hrefs:
            raise RuntimeError("Проверка остановлена: изменились ссылки страницы.")
    if original_script_tail is not None:
        script_start = html.lower().find("<script")
        if script_start < 0 or html[script_start:] != original_script_tail:
            raise RuntimeError("Проверка остановлена: изменился JavaScript или содержимое после него.")
    parser = PageParser()
    parser.feed(html)
    parser.close()


def safe_patch(
    html: str,
    change: dict[str, str],
    original_script_count: int,
    original_hrefs: list[str],
    original_script_tail: str,
) -> tuple[str, str] | None:
    if not isinstance(change, dict):
        return None
    title = str(change.get("title", "Обновление страницы")).strip()
    old = change.get("old")
    new = change.get("new")
    if not isinstance(old, str) or not old or not isinstance(new, str) or not new or old == new:
        return None
    if html.count(old) != 1:
        print(f"Пропуск изменения {title!r}: исходный фрагмент встречается не ровно один раз.")
        return None
    position = html.find(old)
    script_start = html.lower().find("<script")
    if script_start != -1 and position >= script_start:
        print(f"Пропуск изменения {title!r}: попытка изменить JavaScript.")
        return None
    if re.search(r"<\s*/?\s*script\b|javascript\s*:|\bon[a-z]+\s*=", new, re.I):
        print(f"Пропуск изменения {title!r}: небезопасная разметка.")
        return None
    candidate = html.replace(old, new, 1)
    try:
        validate_html(candidate, original_script_count, original_hrefs, original_script_tail)
    except RuntimeError as exc:
        print(f"Пропуск изменения {title!r}: {exc}")
        return None
    return candidate, title


def main() -> int:
    if not TARGET.is_file():
        raise RuntimeError(f"Не найден основной файл сайта: {TARGET.relative_to(ROOT)}")
    initial = TARGET.read_text(encoding="utf-8")
    original_script_count = initial.lower().count("<script")
    original_hrefs = re.findall(r"\bhref\s*=\s*[\"\']([^\"\']*)", initial, re.I)
    original_script_start = initial.lower().find("<script")
    original_script_tail = initial[original_script_start:] if original_script_start >= 0 else ""
    validate_html(initial, original_script_count, original_hrefs, original_script_tail)

    count = random.SystemRandom().randint(1, 15)
    print(f"Случайно выбрано коммитов: {count}")
    changes = request_patches(initial, count)
    if not changes:
        raise RuntimeError("ИИ не предложил ни одного изменения. Коммиты не создавались.")

    committed = 0
    seen_old: set[str] = set()
    for change in changes:
        old = change.get("old") if isinstance(change, dict) else None
        if isinstance(old, str) and old in seen_old:
            print("Пропуск повторного изменения.")
            continue
        if isinstance(old, str):
            seen_old.add(old)
        current = TARGET.read_text(encoding="utf-8")
        result = safe_patch(current, change, original_script_count, original_hrefs, original_script_tail)
        if result is None:
            continue
        updated, title = result
        TARGET.write_text(updated, encoding="utf-8")
        check = run_git("diff", "--check", check=False)
        if check.returncode != 0:
            TARGET.write_text(current, encoding="utf-8")
            print(f"Откат изменения {title!r}: git diff --check не прошёл.")
            continue
        run_git("add", "--", str(TARGET.relative_to(ROOT)))
        staged = run_git("diff", "--cached", "--quiet", check=False)
        if staged.returncode == 0:
            TARGET.write_text(current, encoding="utf-8")
            print(f"Пропуск {title!r}: нет реальных изменений.")
            continue
        if staged.returncode != 1:
            TARGET.write_text(current, encoding="utf-8")
            raise RuntimeError("Не удалось проверить подготовленные изменения Git.")

        safe_title = re.sub(r"[^\wА-Яа-яЁё -]", "", title).strip()[:65]
        if not safe_title:
            safe_title = "Улучшение страницы Minecraft"
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        run_git("commit", "-m", f"Daily improvement ({date}): {safe_title}")
        # Публикуем каждый отдельный коммит, чтобы уже готовые изменения не потерялись,
        # если следующий шаг или следующий коммит завершится ошибкой.
        run_git("push", "origin", "HEAD:main")
        committed += 1
        print(f"Опубликован коммит {committed}: {safe_title}")

    print(f"Готово. Опубликовано осмысленных коммитов: {committed} из выбранных {count}.")
    if committed == 0:
        raise RuntimeError("Ни одно изменение не прошло проверки; коммиты не создавались.")
    if committed < count:
        print("Некоторые изменения пропущены проверками; фиктивные коммиты не создавались.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ОШИБКА: {error}", file=sys.stderr)
        raise SystemExit(1)
