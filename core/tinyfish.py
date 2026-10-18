"""TinyFish integration.

* Search API  (GET  https://api.search.tinyfish.ai?query=...)       -> find demand news for the city
* Web Agent   (POST https://agent.tinyfish.ai/v1/automation/run)     -> read a supplier page, return JSON
Auth header for both: X-API-Key. The key comes from the TINYFISH_API_KEY env var or the Settings page.

Search results are turned into demand signals with plain keyword rules, so every
signal can explain itself ("matched 'sale' -> Zone A x2.5").
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://api.search.tinyfish.ai"
AGENT_URL = "https://agent.tinyfish.ai/v1/automation/run"

# keyword -> (zone code, multiplier, why). First matching rule wins per result.
RULES = [
    (r"\b(diwali|holi|eid|navratri|dussehra|christmas|festival)\b", "D", 2.2, "festival week: staples and gifting go up"),
    (r"\b(flash sale|big sale|mega sale|sale|discount|offer|deal)\b", "A", 2.5, "sale: fast movers spike"),
    (r"\b(ipl|cricket|match|world cup|final)\b", "A", 2.0, "match day: snacks and drinks spike"),
    (r"\b(concert|fest|carnival|mela|event)\b", "A", 1.8, "big event nearby: snacks and drinks"),
    (r"\b(expo|exhibition|conference|summit|hackathon)\b", "C", 1.6, "expo / conference: office and tech items"),
    (r"\b(exam|school reopen|college|admission)\b", "C", 1.5, "exams / school season: stationery"),
    (r"\b(heatwave|heat wave|hot|temperature)\b", "A", 1.7, "heatwave: water and cold drinks"),
    (r"\b(rain|monsoon|flood|storm|waterlogging)\b", "B", 1.4, "rain: people order home supplies instead of going out"),
    (r"\b(pollution|aqi|smog)\b", "B", 1.3, "bad air: people stay in and order household items"),
]

DEFAULT_QUERIES = [
    "{city} sale this week",
    "{city} events this weekend",
    "{city} weather alert rain heatwave",
    "{city} cricket match today",
]


class TinyFishError(Exception):
    pass


def api_key(con=None):
    key = os.environ.get("TINYFISH_API_KEY", "").strip()
    if not key and con is not None:
        r = con.execute("SELECT value FROM settings WHERE key='tinyfish_key'").fetchone()
        key = (r["value"] if r else "") or ""
    return key.strip()


def _request(req, timeout):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        raise TinyFishError(f"TinyFish returned {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise TinyFishError(f"Could not reach TinyFish: {e.reason}") from e
    except TimeoutError as e:
        raise TinyFishError("TinyFish took too long to answer") from e


def search(key, query, location=None, timeout=25):
    params = {"query": query}
    if location:
        params["location"] = location
    req = urllib.request.Request(SEARCH_URL + "?" + urllib.parse.urlencode(params), headers={"X-API-Key": key})
    data = _request(req, timeout)
    return data.get("results", [])


def run_agent(key, url, goal, timeout=150):
    body = json.dumps({"url": url, "goal": goal, "api_integration": "fleetflow"}).encode()
    req = urllib.request.Request(AGENT_URL, data=body, method="POST",
                                 headers={"X-API-Key": key, "Content-Type": "application/json"})
    data = _request(req, timeout)
    if data.get("status") != "COMPLETED":
        err = (data.get("error") or {}).get("message") or "run failed"
        raise TinyFishError(err)
    return data.get("result"), data.get("run_id")


def classify(title, snippet=""):
    """Turn one search result into (zone, multiplier, why) or None."""
    text = f"{title} {snippet}".lower()
    for pattern, zone, mult, why in RULES:
        m = re.search(pattern, text)
        if m:
            return zone, mult, f"matched '{m.group(0)}': {why}"
    return None


def demand_signals(key, city, queries=None):
    """Run the searches and return a de-duplicated list of signal dicts."""
    out, seen = [], set()
    for q in (queries or DEFAULT_QUERIES):
        for r in search(key, q.format(city=city), location=None)[:6]:
            title = (r.get("title") or "").strip()
            if not title or title.lower() in seen:
                continue
            c = classify(title, r.get("snippet", ""))
            if not c:
                continue
            seen.add(title.lower())
            zone, mult, why = c
            out.append({"zone": zone, "multiplier": mult, "title": title[:160], "detail": why,
                        "url": r.get("url", ""), "query": q.format(city=city)})
    return out


PRICE_GOAL = ("Find the product on this page. Return JSON with keys: name (string), price_inr (number), "
              "in_stock (true/false), pack_size (string). If there are several products, return the first one.")
