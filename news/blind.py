"""
И10б (docs/TRADER_PLAN.md): вторая оценка заголовка — без названия монеты, по Glasserman & Lin
(arXiv 2309.17322). Оцениваются только заголовки, которые первая оценка (И10) отнесла к
торгуемым монетам: остальные не дают сигнала ни в одной ветке и не стоят денег. Названия и
тикеры монет заменяются на [COIN]; обезличенный текст хранится вместе с оценкой, чтобы при
проверке было видно, что читала модель. Подсказка и модель заморожены на PROMPT_VERSION —
смена = новая гипотеза. Бюджет общий с И10; первая оценка в цикле идёт раньше второй.
"""
import json
import re
import time
from typing import Dict, List, Optional

from news import scorer

PROMPT_VERSION = "news-blind-v1"
BATCH = scorer.BATCH
MAX_TOKENS = scorer.MAX_TOKENS
PLACEHOLDER = "[COIN]"

# Тикер → названия. Все 27 монет бота (config.SYMBOLS) плюс ходовые; порядок замены — от длинных
# к коротким, чтобы «Binance Coin» ушёл целиком, а «Binance» (биржа) остался.
NAMES = {
    "BTC": ("bitcoin", "xbt"), "ETH": ("ethereum", "ether"), "BNB": ("binance coin",), "SOL": ("solana",),
    "XRP": ("ripple",), "ADA": ("cardano",), "DOGE": ("dogecoin",), "AVAX": ("avalanche",),
    "DOT": ("polkadot",), "POL": ("polygon", "matic"), "LINK": ("chainlink",), "UNI": ("uniswap",),
    "AAVE": ("aave",), "ARB": ("arbitrum",), "OP": ("optimism",), "SUI": ("sui",), "APT": ("aptos",),
    "SHIB": ("shiba inu", "shiba"), "PEPE": ("pepe",), "ATOM": ("cosmos",), "NEAR": ("near protocol",),
    "INJ": ("injective",), "WLD": ("worldcoin",), "TIA": ("celestia",), "RENDER": ("render network", "render token"),
    "EIGEN": ("eigenlayer", "eigen"), "JUP": ("jupiter",), "LTC": ("litecoin",), "TRX": ("tron",),
    "TON": ("toncoin",), "HBAR": ("hedera",), "BCH": ("bitcoin cash",), "ETC": ("ethereum classic",),
    "FIL": ("filecoin",), "ICP": ("internet computer",), "ONDO": ("ondo",), "HYPE": ("hyperliquid",),
    "WIF": ("dogwifhat",), "BONK": ("bonk",), "PENGU": ("pudgy penguins",), "TRUMP": ("official trump",),
    "FET": ("fetch.ai", "artificial superintelligence alliance"), "TAO": ("bittensor",), "SEI": ("sei",),
    "USDT": ("tether",), "USDC": ("usd coin",), "STETH": ("lido staked ether",), "LDO": ("lido",),
    "ENA": ("ethena",), "PYTH": ("pyth",), "KAS": ("kaspa",),
    "ALGO": ("algorand",), "VET": ("vechain",), "XMR": ("monero",), "ZEC": ("zcash",),
}
# Не в словаре намеренно: тикеры-слова (S, MKR «maker», CRV «curve», XLM «stellar») — ложные замены в обычном тексте.

_TICKERS = sorted(NAMES, key=len, reverse=True)
_WORDS = sorted((w for ws in NAMES.values() for w in ws), key=len, reverse=True)
# Тикер — только заглавными и как отдельное слово, допускается $BTC и BTCUSDT / BTC/USDT.
_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])\$?(" + "|".join(map(re.escape, _TICKERS)) + r")(?=USDT?\b|/USDT?\b|\b)")
_NAME_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(map(re.escape, _WORDS)) + r")(?![A-Za-z0-9])", re.I)

SYSTEM = """You label crypto-market news headlines for a trading research log. Labels are later compared with actual price moves, so be calibrated, not dramatic.

The names and tickers of the crypto assets involved have been deliberately replaced with [COIN]. Judge the news itself: what it says happened and how such news usually moves the price of the asset it is about. Do not guess which asset it is.

For every numbered headline return one object:
- id: the headline number;
- direction: "up", "down" or "none" - expected price reaction of the asset the news is about;
- magnitude: 0 none, 1 small (<1%), 2 notable (1-5%), 3 major (>5%) - expected move within the horizon;
- horizon: "minutes", "hours" or "days" - when most of the move should happen;
- confidence: 0..1 - probability that the direction is right;
- novelty: true if the headline reports new information first-hand (announcement, listing, delisting, hack, decision, filing); false for recaps, opinion, analysis, price reports, sponsored content.

Answer with JSON only: {"items": [{"id": 1, "direction": "up", "magnitude": 2, "horizon": "hours", "confidence": 0.6, "novelty": true}]}"""


def anonymize(title: str) -> str:
    """Названия и тикеры монет → [COIN]; соседние плейсхолдеры схлопываются в один."""
    out = _NAME_RE.sub(PLACEHOLDER, title)
    out = _TICKER_RE.sub(PLACEHOLDER, out)
    out = re.sub(r"\[COIN\](?:USDT?|/USDT?)", PLACEHOLDER, out)
    out = re.sub(r"\[COIN\](?:[\s,/&-]|and\s)*\[COIN\]", PLACEHOLDER, out)
    return re.sub(r"\s{2,}", " ", out).strip()


def render(batch: List[dict]) -> str:
    return "\n".join(f"{i}. [{it['source']}] {it['blind_title']}" for i, it in enumerate(batch, 1))


def _label(obj) -> Optional[dict]:
    """Оценка одного заголовка; None — не по схеме. Схема та же, что у И10, без монет."""
    if not isinstance(obj, dict):
        return None
    rows = scorer._item_rows({**obj, "symbols": []})
    if rows is None:
        return None
    probe = scorer._item_rows({**obj, "symbols": ["BTC"]})
    label = dict(probe[0])
    label.pop("symbol")
    return label


def parse_labels(text: Optional[str], n: int) -> Dict[int, dict]:
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
        label = _label(obj)
        if isinstance(num, int) and not isinstance(num, bool) and 1 <= num <= n and label is not None:
            out.setdefault(num, label)
    return out


def score_pending(transport=None, clock=time.time) -> int:
    """Вторая (обезличенная) оценка заголовков, оценённых И10 с монетами; возвращает число оценённых."""
    from ai_trader import client
    import database

    done = 0
    while True:
        batch = database.get_unblinded_news(BATCH)
        if not batch:
            return done
        for it in batch:
            it["blind_title"] = anonymize(it["title"])
        completion = client.complete(client.NEWS_PURPOSE, SYSTEM, render(batch), scorer.model(),
                                     max_tokens=MAX_TOKENS, transport=transport)
        if completion is None:
            return done
        parsed = {} if completion.finish_reason == "length" else parse_labels(completion.text, len(batch))
        now_ms = int(clock() * 1000)
        for i, item in enumerate(batch, 1):
            label = parsed.get(i)
            database.save_news_blind(item["uid"], item["blind_title"], now_ms, "ok" if label else "bad_format",
                                     completion.model, PROMPT_VERSION, label)
            done += 1
