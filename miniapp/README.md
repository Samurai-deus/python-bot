# Мини-апп исследовательской программы

React 19 + Vite + TypeScript + react-query. Экраны: Обзор, Портфели (И14, И18, И13), Данные, Календарь, Система —
всё из одного запроса `/api/research/overview` плюс здоровье бота `/api/system/health` (см. `docs/TRADER_PLAN.md`).

```bash
npm ci
npm run dev        # dev-сервер с прокси /api → VITE_API_URL (по умолчанию http://localhost:8000); нужен initData Telegram
npm run dev:mock   # то же без API и без Telegram: данные из src/api/fixtures/*.json (живой снимок демо-счетов 15.09.2026)
npm test           # vitest
npm run build      # eslint + tsc + vite build → dist/ (режим фикстур в production-сборке не читается)
```

Режим фикстур включается только на dev-сервере (`import.meta.env.DEV`) флагом `VITE_MOCK=1`, который `vite.config.ts` ставит в режиме `mock` — обхода
авторизации на проде нет. Обновить снимок: `docker exec market-bot-api python -c "import json; from api import research_data as rd;
print(json.dumps(rd.overview(), ensure_ascii=False))"` → `src/api/fixtures/overview.json`; тест `tests/test_api_research.py`
сверяет форму фикстуры с ответом API.

Правило запросов: каждый `useQuery` выключается при истёкшей сессии (`enabled: !authExpired`) — проверяет
`tests/test_miniapp_polling_guard.py`.
