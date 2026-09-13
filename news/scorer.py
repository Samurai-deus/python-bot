"""
Оценка заголовков моделью: пачка до BATCH заголовков — один запрос, ответ JSON по каждому.
Бюджет — отдельный от оценки сигналов (client.NEWS_PURPOSE, решение владельца 13.09: 0,3 $ в сутки).
Нет ответа (ключа нет, бюджет исчерпан, сбой) — заголовки ждут следующего цикла; ответ не по
схеме — заголовок помечается bad_format и больше не оценивается.

Подсказка по-английски: заголовки английские, а кириллица дороже в токенах. Версия подсказки
пишется с оценкой — правило И10 заморожено на PROMPT_VERSION, смена = новая гипотеза.
"""
import json
import re
import time
from typing import Dict, List, Optional

PROMPT_VERSION = "news-v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-5"
BATCH = 10
# Ответ ~60 токенов на заголовок, но модель тратит до ~1000 на скрытое рассуждение
# (см. REVIEW_MAX_TOKENS в ai_trader/worker.py); платится только использованное.
MAX_TOKENS = 3000
DIRECTIONS = ("up", "down", "none")
HORIZONS = ("minutes", "hours", "days")
MAX_SYMBOLS = 3
_TICKER = re.compile(r"^[A-Z0-9]{2,15}$")

SYSTEM = """You label crypto-market news headlines for a trading research log. Labels are later compared with actual price moves, so be calibrated, not dramatic.

For every numbered headline return one object:
- id: the headline number;
- symbols: up to 3 base tickers of crypto assets whose price this news should move directly (e.g. "BTC", "SOL"). Market-wide news (macro, Fed, regulation, ETF flows, major exchange incidents) -> ["BTC"]. Nothing tradable -> [];
- direction: "up", "down" or "none" - expected price reaction of those symbols;
- magnitude: 0 none, 1 small (<1%), 2 notable (1-5%), 3 major (>5%) - expected move within the horizon;
- horizon: "minutes", "hours" or "days" - when most of the move should happen;
- confidence: 0..1 - probability that the direction is right;
- novelty: true if the headline reports new information first-hand (announcement, listing, delisting, hack, decision, filing); false for recaps, opinion, analysis, price reports, sponsored content.

Answer with JSON only: {"items": [{"id": 1, "symbols": ["BTC"], "direction": "up", "magnitude": 2, "horizon": "hours", "confidence": 0.6, "novelty": true}]}"""


def model() -> str:
    from utils.env import env_str
    return env_str("AI_MODEL_NEWS", "").strip() or DEFAULT_MODEL


def render(batch: List[dict]) -> str:
    return "\n".join(f"{i}. [{it['source']}] {it['title']}" for i, it in enumerate(batch, 1))


def _item_rows(obj) -> Optional[List[dict]]:
    """Строки оценки по монетам одного заголовка; None — объект не по схеме."""
    if not isinstance(obj, dict):
        return None
    direction = str(obj.get("direction", "")).strip().lower()
    horizon = str(obj.get("horizon", "")).strip().lower()
    magnitude, novelty, symbols = obj.get("magnitude"), obj.get("novelty"), obj.get("symbols")
    if direction not in DIRECTIONS or horizon not in HORIZONS:
        return None
    if isinstance(magnitude, bool) or not isinstance(magnitude, int) or not 0 <= magnitude <= 3:
        return None
    if not isinstance(novelty, bool) or not isinstance(symbols, list):
        return None
    try:
        confidence = float(obj.get("confidence"))
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    tickers = []
    for s in symbols[:MAX_SYMBOLS]:
        t = str(s).strip().upper()
        t = t[:-4] if t.endswith("USDT") and len(t) > 4 else t
        if not _TICKER.match(t):
            return None
        if t not in tickers:
            tickers.append(t)
    return [{"symbol": t, "direction": direction, "magnitude": magnitude, "horizon": horizon,
             "confidence": confidence, "novelty": int(novelty)} for t in tickers]


def parse_scores(text: Optional[str], n: int) -> Dict[int, List[dict]]:
    """{номер заголовка: строки по монетам} только для заголовков, оценённых по схеме."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    out = {}
    for obj in items if isinstance(items, list) else []:
        num = obj.get("id") if isinstance(obj, dict) else None
        rows = _item_rows(obj)
        if isinstance(num, int) and not isinstance(num, bool) and 1 <= num <= n and rows is not None:
            out.setdefault(num, rows)
    return out


def score_pending(transport=None, clock=time.time) -> int:
    """Оценить неоценённые свежие заголовки пачками, пока есть бюджет. Возвращает число оценённых."""
    from ai_trader import client
    import database

    done = 0
    while True:
        batch = database.get_unscored_news(BATCH)
        if not batch:
            return done
        completion = client.complete(client.NEWS_PURPOSE, SYSTEM, render(batch), model(),
                                     max_tokens=MAX_TOKENS, transport=transport)
        if completion is None:
            return done
        parsed = {} if completion.finish_reason == "length" else parse_scores(completion.text, len(batch))
        now_ms = int(clock() * 1000)
        for i, item in enumerate(batch, 1):
            rows = parsed.get(i)
            database.save_news_score(item["uid"], now_ms, "ok" if rows is not None else "bad_format",
                                     completion.model, PROMPT_VERSION, rows or [])
            done += 1
