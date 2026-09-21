# rss-hub — дневной список ИИ-новостей для NotebookLM

Раз в сутки (06:00 МСК) GitHub Actions собирает RSS из `feeds.txt`, берёт новости за 26 часов,
убирает дубли, отдаёт заголовки модели на чистку и рубрикацию и публикует страницу на GitHub Pages.

- Выпуск за день: `https://exrector.github.io/rss-hub/YYYY-MM-DD.html` — **эту ссылку давать NotebookLM**
  (корень/latest у NotebookLM кэшируется как вчерашний контент).
- Последний: `latest.html`, архив: `index.html`.

## Чистка
Цепочка провайдеров, все бесплатные:
0. **GitHub Copilot CLI** прямо в Actions на встроенном `GITHUB_TOKEN` (`permissions: copilot-requests: write`),
   без ключей. Тариф Copilot Free — 200 ед. чата в месяц; один прогон ≈ 0.6 ед. (≈18 ед./мес).
1. Gemini `gemini-2.5-flash` (секрет `GEMINI_API_KEY`, thinking выключен — иначе JSON обрезается).
2. OpenRouter `nvidia/nemotron-3-super-120b-a12b:free` (секрет `OPENROUTER_API_KEY`).
3. Фильтр по ключевым словам.

Ответ модели, оставивший < 10 новостей, считается браком и уходит к следующему провайдеру.
GitHub Models не используется — в сентябре 2026 его выводят из эксплуатации (brownout, HTTP 410).

## Формат
Страница — до 20 главных историй: «заголовок — ссылка на оригинал», без описаний. Внизу — поле со всеми URL:
скопировать и вставить в NotebookLM → Добавить источник → Веб-сайты. Каждая статья становится отдельным источником.

## Источники
Только сайты, которые NotebookLM читает по ссылке сам (`READABLE` в `digest.py`, проверено импортом 2026-09-21):
techcrunch, x.ai, macstories, arstechnica, the-decoder, engadget, zdnet, siliconangle, marktechpost,
huggingface, blog.google, deepmind, simonwillison, 404media, bbc, geekwire. У Techmeme берётся ссылка на оригинал,
если он из этого списка.

Не читает (0 символов после импорта): theverge, wired, axios, openai.com, reuters, bloomberg, technologyreview,
nytimes, cnbc, wsj; ссылки Google News — редиректы. Новый сайт добавлять только после проверки импортом.

Локальный запуск: `GEMINI_API_KEY=... python3 digest.py` (только stdlib).
