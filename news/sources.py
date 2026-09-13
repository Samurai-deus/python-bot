"""
Источники новостей: RSS/Atom-ленты, объявления Bybit и Binance (листинги, делистинги).
Все проверены с сервера бота 13.09.2026 — отвечают напрямую, без прокси.

Заголовок — словарь {uid, source, title, url, published_ms}; uid уникален в пределах
источника (guid/ссылка ленты, url объявления Bybit, code статьи Binance).
"""
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_BINANCE = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
            "?type=1&catalogId={}&pageNo=1&pageSize=20")
SOURCES = (
    ("coindesk", "rss", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "rss", "https://cointelegraph.com/rss"),
    ("theblock", "rss", "https://www.theblock.co/rss.xml"),
    ("fed", "rss", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("bybit", "bybit", "https://api.bybit.com/v5/announcements/index?locale=en-US&limit=20"),
    ("binance_listing", "binance", _BINANCE.format(48)),
    ("binance_delisting", "binance", _BINANCE.format(161)),
)
TITLE_MAX = 400


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, *names):
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _text(el, *names) -> str:
    c = _child(el, *names)
    return (c.text or "").strip() if c is not None else ""


def _clean(title: str) -> str:
    return " ".join(title.split())[:TITLE_MAX]


def parse_time(value: str) -> Optional[int]:
    """RFC 822 (RSS pubDate) или ISO 8601 (Atom) → мс UTC; без пояса — UTC; не разобрать — None."""
    if not value:
        return None
    for parse in (parsedate_to_datetime, lambda v: datetime.fromisoformat(v.replace("Z", "+00:00"))):
        try:
            dt = parse(value.strip())
        except (TypeError, ValueError, IndexError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    return None


def parse_feed(source: str, content: bytes) -> List[dict]:
    """Элементы RSS (item) и Atom (entry)."""
    out = []
    for el in ET.fromstring(content).iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = _clean(_text(el, "title"))
        link_el = _child(el, "link")
        link = ((link_el.text or "").strip() or link_el.get("href", "")) if link_el is not None else ""
        guid = _text(el, "guid", "id") or link or title
        if title:
            out.append({"uid": f"{source}:{guid}"[:500], "source": source, "title": title,
                        "url": link or None, "published_ms": parse_time(_text(el, "pubDate", "published", "updated"))})
    return out


def parse_bybit(source: str, data: dict) -> List[dict]:
    out = []
    for a in (data.get("result") or {}).get("list") or []:
        when = a.get("publishTime") or a.get("dateTimestamp")
        out.append({"uid": f"{source}:{a['url']}", "source": source, "title": _clean(a["title"]),
                    "url": a["url"], "published_ms": int(when) if when else None})
    return out


def parse_binance(source: str, data: dict) -> List[dict]:
    cats = (data.get("data") or {}).get("catalogs") or []
    out = []
    for a in (cats[0].get("articles") or []) if cats else []:
        out.append({"uid": f"binance:{a['code']}", "source": source, "title": _clean(a["title"]),
                    "url": f"https://www.binance.com/en/support/announcement/{a['code']}",
                    "published_ms": int(a["releaseDate"]) if a.get("releaseDate") else None})
    return out


_PARSERS = {"rss": parse_feed, "bybit": parse_bybit, "binance": parse_binance}


def fetch_all(http) -> Tuple[List[dict], int]:
    """Заголовки всех источников и число ответивших. Сбой источника не мешает остальным."""
    items, ok = [], 0
    for name, kind, url in SOURCES:
        try:
            r = http.get(url)
            r.raise_for_status()
            items += _PARSERS[kind](name, r.content if kind == "rss" else r.json())
            ok += 1
        except Exception as exc:  # сеть, формат ответа — источник пропускается до следующего опроса
            logger.warning("news: источник %s не ответил: %s", name, type(exc).__name__)
    return items, ok
