from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from urllib.parse import quote_plus
import pandas as pd
import re
import requests
import xml.etree.ElementTree as ET

from market_intelligence.signals.retail_context import source_confidence
from market_intelligence.util.clock import utc_now


# The gnews package makes network calls with no timeout of its own and can block a run
# indefinitely (the pipeline then sits on "Collecting and deduplicating retail news"), so it
# is only a fallback and is always time-boxed.
GNEWS_PACKAGE_TIMEOUT_SECONDS = 25
GOOGLE_NEWS_RSS_TIMEOUT_SECONDS = 15
GNEWS_PARALLEL_REQUESTS = 4


def gnews_package_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> Optional[List[Dict[str, Any]]]:
    try:
        from gnews import GNews
    except ImportError:
        return None

    def _fetch() -> List[Dict[str, Any]]:
        google_news = GNews(language=language, country=country, period=period, max_results=max_results)
        return google_news.get_news(keyword)

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_fetch)
    try:
        return future.result(timeout=GNEWS_PACKAGE_TIMEOUT_SECONDS)
    except FutureTimeout as exc:
        raise TimeoutError(f"gnews package did not answer within {GNEWS_PACKAGE_TIMEOUT_SECONDS}s") from exc
    finally:
        executor.shutdown(wait=False)


def google_news_rss_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> List[Dict[str, Any]]:
    # Google News RSS supports a "when:" query operator. GNews package is preferred when installed.
    query = quote_plus(f"{keyword} when:{period}")
    country_code = country.upper()
    lang_code = language.lower()
    url = f"https://news.google.com/rss/search?q={query}&hl={lang_code}-{country_code}&gl={country_code}&ceid={country_code}:{lang_code}"
    response = requests.get(url, timeout=GOOGLE_NEWS_RSS_TIMEOUT_SECONDS)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    articles = []
    for item in root.findall(".//item")[:max_results]:
        source_node = item.find("source")
        articles.append(
            {
                "title": item.findtext("title", default=""),
                "description": item.findtext("description", default=""),
                "published date": item.findtext("pubDate", default=""),
                "url": item.findtext("link", default=""),
                "publisher": source_node.text if source_node is not None else "",
            }
        )
    return articles


def clean_news_description(description: str) -> str:
    text = re.sub(r"<[^>]+>", " ", description or "")
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def article_days_old(published_date: str) -> Optional[int]:
    if not published_date:
        return None
    try:
        published = parsedate_to_datetime(published_date)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).days)
    except (TypeError, ValueError):
        return None


# Whole-word matches, so "port" does not fire on "support" or "oil" on "boil".
WEATHER_TERMS = (
    "hurricane", "tropical storm", "tornado", "tornadoes", "flood", "flooding", "floods", "severe weather",
    "winter storm", "blizzard", "hail", "wildfire", "heat wave", "storm", "storms",
)
SUPPLY_DISRUPTION_TERMS = (
    "shipping", "red sea", "houthi", "houthis", "port", "ports", "strike", "supply chain", "supply chains",
    "blockade", "shortage", "shortages", "disruption", "disruptions", "congestion", "embargo", "sanctions",
    "war", "conflict", "logistics", "trucking", "diesel", "oil",
)


def _mentions(text: str, terms: Tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)


def classify_news_title(title: str, description: str) -> Tuple[str, float, str]:
    text = f"{title} {description}".lower()
    if any(word in text for word in ["recall", "contamination", "lawsuit", "closure", "closing", "tariff", "warning"]):
        return "risk_event", 7.0, "negative"
    if _mentions(text, WEATHER_TERMS):
        return "weather_disruption", 6.0, "negative"
    if _mentions(text, SUPPLY_DISRUPTION_TERMS):
        return "supply_disruption", 6.5, "negative"
    if any(word in text for word in ["inflation", "prices", "freight", "cost", "margin"]):
        return "price_pressure", 6.0, "negative"
    if any(word in text for word in ["deal", "sale", "promotion", "coupon", "holiday", "seasonal"]):
        return "demand_opportunity", 6.5, "positive"
    if any(word in text for word in ["earnings", "forecast", "guidance", "outlook"]):
        return "financial_update", 5.5, "neutral"
    return "general_market_news", 3.5, "neutral"


def collect_gnews(keywords: List[str], country: str, language: str, period: str, max_results: int, retailer: str = "Retailer") -> Dict[str, Any]:
    all_articles = []
    errors = []
    per_keyword_limit = max(1, int(max_results / max(1, len(keywords))))
    def _fetch_keyword(keyword: str) -> Tuple[str, Optional[List[Dict[str, Any]]], Optional[Exception]]:
        # The Google News RSS feed is the primary source: it answers in a couple of seconds
        # and has a hard timeout. The gnews package is only used if the feed fails.
        try:
            try:
                return keyword, google_news_rss_collect(keyword, country, language, period, per_keyword_limit), None
            except Exception:
                articles = gnews_package_collect(keyword, country, language, period, per_keyword_limit)
                if articles is None:
                    raise
                return keyword, articles, None
        except Exception as exc:
            return keyword, None, exc

    # Keywords are independent requests, so fetch several at once; results keep keyword order.
    with ThreadPoolExecutor(max_workers=GNEWS_PARALLEL_REQUESTS) as pool:
        fetched = list(pool.map(_fetch_keyword, keywords))

    for keyword, articles, fetch_error in fetched:
        if fetch_error is not None:
            errors.append(f"{keyword}: {fetch_error}")
            continue
        try:
            for article in articles or []:
                description = clean_news_description(article.get("description", ""))
                event_type, score, sentiment = classify_news_title(article.get("title", ""), description)
                publisher = article.get("publisher", "")
                if isinstance(publisher, dict):
                    publisher = publisher.get("title") or publisher.get("href") or ""
                published_date = article.get("published date") or article.get("published_date", "")
                days_old = article_days_old(published_date)
                if days_old is not None and days_old > 30:
                    score = max(1.0, score - 1.0)
                all_articles.append(
                    {
                        "keyword": keyword,
                        "title": article.get("title", ""),
                        "description": description,
                        "published_date": published_date,
                        "days_old": days_old,
                        "publisher": publisher,
                        "url": article.get("url", ""),
                        "source_tier": source_confidence(str(publisher)),
                        "event_type": event_type,
                        "sentiment": sentiment,
                        "risk_score": score,
                        "confidence": source_confidence(str(publisher)),
                    }
                )
        except Exception as exc:
            errors.append(f"{keyword}: {exc}")

    deduped = []
    seen = set()
    for article in all_articles:
        key = article["url"] or article["title"]
        if key and key not in seen:
            seen.add(key)
            deduped.append(article)

    if not deduped:
        return {
            "status": "failed" if errors else "empty",
            "source": "GNews",
            "error": "; ".join(errors) if errors else "No meaningful articles returned for selected keywords.",
            "raw": [],
            "rows": [],
            "items": [],
        }

    score = round(float(pd.Series([a["risk_score"] for a in deduped]).mean()), 2) if deduped else 1.0
    negative_count = sum(1 for article in deduped if article.get("sentiment") == "negative")
    high_conf_count = sum(1 for article in deduped if article.get("confidence") == "High")
    event_counts = pd.Series([article.get("event_type", "unknown") for article in deduped]).value_counts().to_dict()
    signal = {
        "date": utc_now()[:10],
        "retailer": retailer,
        "region": country.upper(),
        "region_scope": "country_news",
        "source": "GNews / Google News RSS",
        "signal_area": "Retail News",
        "signal_name": "news_risk_score",
        "signal_value": len(deduped),
        "risk_score": score,
        "confidence": "Medium",
        "score_reason": (
            f"Average article risk across {len(deduped)} deduplicated articles; {negative_count} negative; "
            f"{high_conf_count} from high-confidence publishers. Event mix: "
            + (
                ", ".join(
                    f"{str(event_type).replace('_', ' ')} {count}"
                    for event_type, count in event_counts.items()
                )
                or "none classified"
            )
            + "."
        ),
        "business_impact": "Recent news can reveal competitor moves, pricing pressure, recalls, store changes, and supply-chain risk.",
        "recommended_action": "Review the high-risk articles with the category owner before executive distribution.",
        "raw_reference": f"{len(deduped)} articles",
    }
    return {"status": "success", "source": "GNews", "error": "; ".join(errors), "raw": deduped, "rows": [signal], "items": deduped}
