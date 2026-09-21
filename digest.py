#!/usr/bin/env python3
"""Дневной AI-дайджест из RSS: сбор → чистка (GitHub Models или ключевые слова) → docs/YYYY-MM-DD.html.

Только stdlib. Модели (по очереди): GitHub Copilot CLI на встроенном токене Actions → Gemini (GEMINI_API_KEY)
→ OpenRouter free (OPENROUTER_API_KEY) → ключевые слова.
GitHub Models не используем — сервис выводят из эксплуатации (сентябрь 2026).
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
MSK = timezone(timedelta(hours=3))
WINDOW_HOURS = 26
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
# (имя, URL OpenAI-совместимого API, переменная с ключом, модель, доп. параметры)
PROVIDERS = [
    # thinking выключен: иначе размышления съедают max_tokens и JSON обрезается
    ("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
     "GEMINI_API_KEY", "gemini-2.5-flash", {"reasoning_effort": "none"}),
    ("OpenRouter", "https://openrouter.ai/api/v1/chat/completions",
     "OPENROUTER_API_KEY", "nvidia/nemotron-3-super-120b-a12b:free", {}),
]
MIN_KEPT = 10  # модель, оставившая меньше, считается сбойной
MAX_LLM_ITEMS = 160

KEYWORDS = re.compile(
    r"\b(AI|A\.I\.|artificial intelligence|machine learning|LLMs?|GPT[-\w.]*|ChatGPT|OpenAI|Anthropic|Claude|"
    r"Gemini|DeepMind|Mistral|Llama|Meta AI|xAI|Grok|Copilot|Nvidia|chatbots?|neural|agents?|agentic|"
    r"generative|model|models|inference|GPUs?|datacenters?|data centers?|robot\w*|superintelligence|AGI)\b",
    re.I,
)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, */*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def local(tag):
    return tag.rsplit("}", 1)[-1]


def child_text(el, *names):
    for c in el:
        if local(c.tag) in names and (c.text or "").strip():
            return c.text
    return ""


def parse_feed(data, source):
    items = []
    root = ET.fromstring(data)
    for el in root.iter():
        kind = local(el.tag)
        if kind not in ("item", "entry"):
            continue
        title = strip_tags(child_text(el, "title"))
        link = child_text(el, "link").strip()
        if not link:  # Atom: <link href=...>
            for c in el:
                if local(c.tag) == "link" and c.get("rel", "alternate") == "alternate":
                    link = c.get("href", "")
                    break
        date = parse_date(child_text(el, "pubDate", "published", "updated", "date"))
        summary = strip_tags(child_text(el, "description", "summary", "content"))
        via = source
        if "news.google.com" in link:  # у Google News в заголовке « - Издание»
            m = re.match(r"(.*) - ([^-]+)$", title)
            if m:
                title, via = m.group(1).strip(), f"{source} ({m.group(2).strip()})"
            summary = ""
        if title and link:
            items.append({"title": title, "link": link, "date": date, "source": via, "summary": summary[:400]})
    return items


def norm(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower())[:80]


def collect():
    feeds = []
    for line in (ROOT / "feeds.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "|" in line:
            name, url = (p.strip() for p in line.split("|", 1))
            feeds.append((name, url))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    out, seen, stats = [], set(), []
    for name, url in feeds:
        try:
            items = parse_feed(fetch(url), name)
        except Exception as e:  # noqa: BLE001 — одна лента не должна валить сборку
            stats.append(f"{name}: ОШИБКА {type(e).__name__}: {e}")
            continue
        fresh = [i for i in items if i["date"] and i["date"] >= cutoff]
        added = 0
        for i in fresh:
            key = norm(i["title"])
            if key not in seen:
                seen.add(key)
                out.append(i)
                added += 1
        stats.append(f"{name}: всего {len(items)}, за {WINDOW_HOURS}ч {len(fresh)}, новых {added}")
    out.sort(key=lambda i: i["date"], reverse=True)
    return out, stats


def ask_model(url, key, model, prompt, extra):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 8000,
        "response_format": {"type": "json_object"},
        **extra,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.load(r)
    text = resp["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", text, re.S)  # на случай ```json ... ```
    return json.loads(m.group(0) if m else text)


def ask_copilot(prompt):
    # В Actions авторизуется встроенным GITHUB_TOKEN (нужно permissions: copilot-requests: write)
    r = subprocess.run(["copilot", "-p", prompt, "-s"], capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError(f"copilot exit {r.returncode}: {r.stderr.strip()[:300]}")
    m = re.search(r"\{.*\}", r.stdout, re.S)
    return json.loads(m.group(0) if m else r.stdout)


def llm_clean(items):
    items = items[:MAX_LLM_ITEMS]
    listing = "\n".join(f"{n}. [{i['source']}] {i['title']}" for n, i in enumerate(items))
    prompt = (
        "Ниже заголовки техно-новостей за сутки. Задача:\n"
        "1) Оставь только новости про ИИ (модели, компании ИИ, чипы и дата-центры для ИИ, регулирование ИИ, "
        "применение ИИ). Выкинь рекламу, распродажи, обзоры гаджетов без ИИ, подкасты, дайджесты-сборники.\n"
        "2) Если одна история встречается несколько раз — оставь один номер (самый информативный заголовок).\n"
        "3) Разложи по 4-7 рубрикам с короткими русскими названиями, внутри рубрики — по важности.\n"
        'Ответ строго JSON: {"sections":[{"name":"...","ids":[0,5]}]}\n\n' + listing
    )
    errors = []
    chain = [("Copilot", None, "GITHUB_TOKEN", "copilot (auto)", None)] if shutil.which("copilot") else []
    for name, url, env, model, extra in chain + PROVIDERS:
        key = os.environ.get(env)
        if not key:
            errors.append(f"{name}: нет {env}")
            continue
        try:
            data = ask_copilot(prompt) if url is None else ask_model(url, key, model, prompt, extra)
        except Exception as e:  # noqa: BLE001 — пробуем следующего провайдера
            detail = e.read().decode()[:300] if hasattr(e, "read") else ""
            errors.append(f"{name}: {type(e).__name__}: {e} {detail}")
            print(f"[llm] {errors[-1]}", flush=True)
            continue
        sections, used = [], set()
        for s in data.get("sections", []):
            picked = []
            for i in s.get("ids", []):
                if isinstance(i, int) and 0 <= i < len(items) and i not in used:
                    used.add(i)
                    picked.append(items[i])
            if picked:
                sections.append((str(s.get("name", "Разное")), picked))
        if len(used) >= MIN_KEPT:
            print(f"[llm] {name} {model}: {len(items)} -> {len(used)}", flush=True)
            return sections, f"модель {model}"
        errors.append(f"{name}: оставила {len(used)} — брак")
        print(f"[llm] {errors[-1]}", flush=True)
    raise RuntimeError("; ".join(errors))


def keyword_clean(items):
    kept = [i for i in items if KEYWORDS.search(i["title"] + " " + i["summary"])]
    print(f"[keywords] {len(items)} -> {len(kept)}", flush=True)
    return [("Новости ИИ", kept)]


def render(day, sections, method):
    total = sum(len(v) for _, v in sections)
    parts = [
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">",
        f"<title>AI-дайджест {day}</title>",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:820px;margin:2em auto;padding:0 16px;color:#222}"
        "h2{margin-top:1.8em;border-bottom:1px solid #ddd}li{margin:.6em 0}.m{color:#777;font-size:.85em}"
        "@media(prefers-color-scheme:dark){body{background:#111;color:#ddd}a{color:#8ab4f8}h2{border-color:#333}}</style>",
        "</head><body>",
        f"<h1>AI-дайджест за {day}</h1>",
        f"<p class=\"m\">{total} новостей за последние {WINDOW_HOURS} ч. Отбор: {method}.</p>",
    ]
    for name, items in sections:
        parts.append(f"<h2>{html.escape(name)}</h2><ol>")
        for i in items:
            t = i["date"].astimezone(MSK).strftime("%d.%m %H:%M")
            parts.append(
                f"<li><a href=\"{html.escape(i['link'])}\">{html.escape(i['title'])}</a>"
                f"<br><span class=\"m\">{html.escape(i['source'])} · {t} МСК</span>"
            )
            if i["summary"]:
                parts.append(f"<br>{html.escape(i['summary'][:300])}")
            parts.append("</li>")
        parts.append("</ol>")
    parts.append("</body></html>")
    return "\n".join(parts)


def write_index():
    days = sorted((p.stem for p in DOCS.glob("20??-??-??.html")), reverse=True)
    links = "".join(f"<li><a href=\"{d}.html\">{d}</a></li>" for d in days)
    (DOCS / "index.html").write_text(
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>AI-дайджест</title>"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"></head>"
        f"<body style=\"font:16px system-ui;max-width:600px;margin:2em auto;padding:0 16px\">"
        f"<h1>AI-дайджест — архив</h1><p><a href=\"latest.html\">Последний выпуск</a></p><ul>{links}</ul></body></html>"
    )


def main():
    day = datetime.now(MSK).strftime("%Y-%m-%d")
    items, stats = collect()
    print("\n".join(stats), flush=True)
    if not items:
        sys.exit("ни одной свежей новости — страница не обновлена")
    try:
        sections, method = llm_clean(items)
    except Exception as e:  # noqa: BLE001
        print(f"[llm] сбой: {type(e).__name__}: {e}", flush=True)
        sections, method = keyword_clean(items), "фильтр по ключевым словам (модель недоступна)"
    page = render(day, sections, method)
    DOCS.mkdir(exist_ok=True)
    (DOCS / f"{day}.html").write_text(page)
    (DOCS / "latest.html").write_text(page)
    (DOCS / ".nojekyll").touch()
    write_index()
    print(f"готово: docs/{day}.html", flush=True)


if __name__ == "__main__":
    main()
