#!/usr/bin/env python3
"""Fetch Quasi ad-level performance data from Triple Whale, parse the ad
naming convention, and emit enriched JSON for the dashboard.

Runs in two contexts:
- Local dev: reads env var TRIPLEWHALE_API_KEY (optionally TRIPLEWHALE_SHOP_ID)
- GitHub Actions: same env vars, populated from repo secrets

Data source: the Data-Out custom-SQL endpoint against the `pixel_joined_tvf`
table, which carries BOTH lenses per row:
  - channel-reported (spend, impressions, clicks, conversions, conversion value)
    -> the headline lens, matches Meta Ads Manager (same role as IM8's meta_*)
  - Triple Whale Pixel-attributed (order_revenue, orders_quantity, new-customer
    splits, visitor counts) -> the alternate lens. Stored under the nb_* keys
    IM8 used for Northbeam so the frontend needs zero changes.

Attribution for the pixel lens: Triple Attribution model, 7-day window — the
closest thing to IM8's Northbeam clicks+modeled-views 7d.

Creation dates: Quasi ad names do NOT reliably embed dates, so an ad's `date`
(YYMMDD, IM8's format) is derived from the earliest daily row we ever saw for
it. That first-seen map is persisted to data/first_seen.json so ads keep their
birthdate after they age past the lookback window.

Output written to public/data/latest.json (overwritten each run). No history
snapshots are committed — that bloated the IM8 repo past 1GB.

Offline mode for development:  python3 fetch_triplewhale.py --csv export.csv
parses a Triple Whale UI export instead of calling the API.
"""
import csv
import json
import os
import pathlib
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib import request as urlreq
from urllib.error import HTTPError

from parse_ad_name import parse_ad_name

API_BASE = "https://api.triplewhale.com/api/v2"

# --- Configuration ---------------------------------------------------------
# Shop id candidates, tried in order until one returns data. The Triple Whale
# API keys are shop-scoped; docs say shop_id "often corresponds to the shop
# domain" but the UI export filename used the myshopify domain, so we probe.
SHOP_ID_CANDIDATES = [
    s for s in [
        os.environ.get("TRIPLEWHALE_SHOP_ID"),
        "officialquasi.com",
        "e1c5f3-7e.myshopify.com",
    ] if s
]

# Pixel lens: Triple Attribution, 7-day window (dashboard's alternate lens).
ATTRIBUTION_MODEL = "Triple Attribution"
ATTRIBUTION_WINDOW = "7_days"

CHANNELS = ["facebook-ads", "Meta"]   # Meta only for now, either spelling
LOOKBACK_DAYS = 180
CHUNK_DAYS = 30                        # per-request date window for the SQL API

SQL_TEMPLATE = """
SELECT
  event_date,
  ad_id,
  any(ad_name) AS ad_name,
  any(campaign_name) AS campaign_name,
  any(adset_name) AS adset_name,
  SUM(spend) AS spend,
  SUM(impressions) AS impressions,
  SUM(clicks) AS clicks,
  SUM(channel_reported_conversions) AS meta_txns,
  SUM(channel_reported_conversion_value) AS meta_rev,
  SUM(orders_quantity) AS pixel_txns,
  SUM(order_revenue) AS pixel_rev,
  SUM(new_customer_orders) AS nc_txns,
  SUM(new_customer_order_revenue) AS nc_rev,
  SUM(new_visitors) AS new_visits,
  SUM(unique_visitors) AS visits
FROM pixel_joined_tvf
WHERE event_date BETWEEN @startDate AND @endDate
  AND channel IN ({channels})
{lens_filter}
GROUP BY event_date, ad_id
"""

LENS_FILTER = (
    "  AND model = '{model}'\n  AND attribution_window = '{window}'"
)


def _api_key() -> str:
    key = os.environ.get("TRIPLEWHALE_API_KEY")
    if not key:
        sys.exit("ERROR: TRIPLEWHALE_API_KEY not set")
    return key


def _request(method: str, path: str, body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"x-api-key": _api_key(), "Content-Type": "application/json"}
    req = urlreq.Request(API_BASE + path, data=data, headers=headers, method=method)
    for attempt in range(5):
        try:
            with urlreq.urlopen(req, timeout=300) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            body_txt = e.read().decode()[:400]
            if e.code == 429 and attempt < 4:
                wait = int(e.headers.get("Retry-After") or 15) + 5 * attempt
                print(f"  rate-limited, waiting {wait}s…", flush=True)
                time.sleep(wait)
                continue
            if e.code >= 500 and attempt < 4:
                wait = 20 * (attempt + 1)
                print(f"  HTTP {e.code}, retrying in {wait}s… {body_txt[:120]}", flush=True)
                time.sleep(wait)
                continue
            print(f"  HTTP {e.code}: {body_txt}", flush=True)
            raise
    raise RuntimeError("exhausted retries")


def verify_key() -> None:
    """Fail fast (with a useful message) if the API key is bad."""
    try:
        res = _request("GET", "/users/api-keys/me")
        scopes = res.get("scopes") or res.get("permissions") or ""
        print(f"  API key OK. {json.dumps(res)[:200]}")
        if scopes and "data" not in json.dumps(res).lower():
            print("  WARNING: key metadata doesn't mention a data scope — SQL access may be plan-gated.")
    except HTTPError:
        sys.exit("ERROR: Triple Whale API key rejected by /users/api-keys/me")


def run_sql(shop_id: str, query: str, start: str, end: str) -> list:
    payload = {
        "shopId": shop_id,
        "query": query,
        "period": {"startDate": start, "endDate": end},
    }
    res = _request("POST", "/orcabase/api/sql", payload)
    if not res.get("success", True) and "data" not in res:
        raise RuntimeError(f"SQL query failed: {json.dumps(res)[:300]}")
    return res.get("data") or []


def build_query(with_lens_filter: bool) -> str:
    channels = ", ".join(f"'{c}'" for c in CHANNELS)
    lens = (
        LENS_FILTER.format(model=ATTRIBUTION_MODEL, window=ATTRIBUTION_WINDOW)
        if with_lens_filter else ""
    )
    return SQL_TEMPLATE.format(channels=channels, lens_filter=lens)


def fetch_daily_rows(shop_id: str, start_day, end_day) -> tuple:
    """Pull daily × ad rows in CHUNK_DAYS windows, most recent window first
    so a wrong shop id fails fast. Returns (rows, lens_filtered); rows is None
    when this shop id errors or comes back empty on the first chunk (caller
    then tries the next candidate)."""
    windows = []
    cur = start_day
    while cur <= end_day:
        chunk_end = min(cur + timedelta(days=CHUNK_DAYS - 1), end_day)
        windows.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    windows.reverse()  # newest first — live shops always have recent rows

    rows = []
    lens_filtered = True
    for i, (ws, we) in enumerate(windows):
        s, e = ws.isoformat(), we.isoformat()
        while True:
            try:
                chunk = run_sql(shop_id, build_query(lens_filtered), s, e)
                break
            except Exception as ex:
                if lens_filtered:
                    # model/attribution_window WHERE-filters can be flaky on
                    # the simulated view — retry on the table defaults
                    # (Triple Attribution + lifetime window) and record that.
                    print(f"  lens-filtered query failed ({str(ex)[:120]}); retrying with table defaults")
                    lens_filtered = False
                    continue
                if i == 0:
                    print(f"  shop id {shop_id!r} failed: {str(ex)[:160]}")
                    return None, lens_filtered
                raise
        print(f"  {s} → {e}: {len(chunk)} rows")
        if i == 0 and not chunk:
            # Probably the wrong shop id — let the caller try the next one.
            print(f"  shop id {shop_id!r} returned no rows for the most recent window")
            return None, lens_filtered
        rows.extend(chunk)
    return rows, lens_filtered


# --- Offline CSV mode -------------------------------------------------------

_MONEY_RE = re.compile(r"[^0-9.\-]")


def _num(v) -> float:
    """Parse numbers that may arrive as '$540.82', '1,234', '', or floats."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = _MONEY_RE.sub("", str(v))
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def rows_from_csv(paths: list) -> list:
    """Map Triple Whale UI-export CSVs onto the same row shape the SQL API
    returns, so the rest of the pipeline is identical. The UI export has no
    raw visitor counts (only Pixel New Visitor Percent) and no raw
    channel-reported purchase count (derive txns = spend / CPA)."""
    rows = []
    for path in paths:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                spend = _num(r.get("Ad Spend"))
                cpa = _num(r.get("CPA"))
                rows.append({
                    "event_date": (r.get("Date") or "")[:10],
                    "ad_id": (r.get("Ad Id") or "").strip(),
                    "ad_name": (r.get("Ad Name") or "").strip(),
                    "campaign_name": r.get("Campaign Name") or "",
                    "adset_name": r.get("Adset Name") or "",
                    "spend": spend,
                    "impressions": _num(r.get("Impressions")),
                    "clicks": _num(r.get("Clicks")),
                    "meta_txns": (spend / cpa) if cpa else 0.0,
                    "meta_rev": _num(r.get("CV")),
                    "pixel_txns": _num(r.get("Pixel Purchases")),
                    "pixel_rev": _num(r.get("Pixel CV")),
                    "nc_txns": _num(r.get("Pixel New Customer Purchases")),
                    "nc_rev": _num(r.get("Pixel New Customer CV")),
                    "new_visits": 0.0,
                    "visits": 0.0,
                })
    return rows


# --- First-seen sidecar ------------------------------------------------------

def load_first_seen(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def update_first_seen(first_seen: dict, rows: list) -> dict:
    """Merge the earliest event_date per ad_id into the sidecar. Existing
    (older) entries always win — that's the whole point of persisting it."""
    for r in rows:
        ad_id = str(r.get("ad_id") or "").strip()
        day = str(r.get("event_date") or "")[:10]
        if not ad_id or not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
            continue
        prev = first_seen.get(ad_id)
        if prev is None or day < prev:
            first_seen[ad_id] = day
    return first_seen


def iso_to_yymmdd(day: Optional[str]) -> Optional[str]:
    if not day or not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        return None
    return day[2:4] + day[5:7] + day[8:10]


# --- Aggregation -------------------------------------------------------------

METRIC_KEYS = (
    "spend", "impressions", "clicks", "meta_rev", "meta_txns",
    "nb_rev", "nb_txns", "nc_rev", "nc_txns", "visits", "new_visits",
)


def aggregate_rows(rows: list, mappings: Optional[dict] = None,
                   first_seen: Optional[dict] = None) -> list:
    """Collapse daily × ad rows into one parsed+enriched record per ad.

    Ads are grouped by normalized name (dedup_key) like IM8 — names get
    edited and re-uploaded, so several ad_ids can be one creative — but every
    contributing ad_id is kept in meta_ad_ids.
    """
    first_seen = first_seen or {}
    by_ad: dict[str, dict] = {}
    for r in rows:
        raw_name = (r.get("ad_name") or "").strip()
        ad_id = str(r.get("ad_id") or "").strip()
        if not raw_name and not ad_id:
            continue

        parsed = parse_ad_name(raw_name)
        dedup = parsed["dedup_key"] or raw_name or ad_id

        metrics = {
            "spend": _num(r.get("spend")),
            "impressions": _num(r.get("impressions")),
            "clicks": _num(r.get("clicks")),
            # Channel-reported lens (matches Meta Ads Manager) — headline.
            "meta_rev": _num(r.get("meta_rev")),
            "meta_txns": _num(r.get("meta_txns")),
            # TW Pixel-attributed lens. Keys keep IM8's nb_* names (nb was
            # "Northbeam") so the frontend needs zero changes.
            "nb_rev": _num(r.get("pixel_rev")),
            "nb_txns": _num(r.get("pixel_txns")),
            # New-customer pixel lens — TW has it, Northbeam didn't. Stored
            # for a future dashboard column.
            "nc_rev": _num(r.get("nc_rev")),
            "nc_txns": _num(r.get("nc_txns")),
            # Raw visitor counts so %-new computes correctly at any level.
            "visits": _num(r.get("visits")),
            "new_visits": _num(r.get("new_visits")),
        }

        if dedup in by_ad:
            agg = by_ad[dedup]
            for k in METRIC_KEYS:
                agg["metrics"][k] += metrics[k]
            agg["meta_campaigns"].add(r.get("campaign_name") or "")
            agg["meta_adsets"].add(r.get("adset_name") or "")
            if ad_id:
                agg["meta_ad_ids"].add(ad_id)
        else:
            by_ad[dedup] = {
                **parsed,
                "metrics": metrics,
                "meta_campaigns": {r.get("campaign_name") or ""},
                "meta_adsets": {r.get("adset_name") or ""},
                "meta_ad_ids": {ad_id} if ad_id else set(),
            }

    out = []
    for rec in by_ad.values():
        m = rec["metrics"]
        # Derived metrics on the headline (channel-reported) lens, as IM8 does.
        m["roas"] = round(m["meta_rev"] / m["spend"], 4) if m["spend"] else 0
        m["cpm"] = round((m["spend"] / m["impressions"]) * 1000, 4) if m["impressions"] else 0
        m["cpa"] = round(m["spend"] / m["meta_txns"], 4) if m["meta_txns"] else None
        m["aov"] = round(m["meta_rev"] / m["meta_txns"], 2) if m["meta_txns"] else None
        m["ctr_raw"] = round(m["clicks"] / m["impressions"], 6) if m["impressions"] else 0
        # Mirror into primary rev/transactions for the UI default.
        m["rev"] = m["meta_rev"]
        m["transactions"] = m["meta_txns"]

        # Creation date = earliest day any of this creative's ad_ids was seen.
        days = [first_seen[x] for x in rec["meta_ad_ids"] if x in first_seen]
        rec["date"] = iso_to_yymmdd(min(days)) if days else None

        if mappings:
            for dim, mapping in mappings.items():
                if dim in rec and rec[dim] in mapping:
                    rec[dim] = mapping[rec[dim]]

        rec["meta_campaigns"] = sorted(x for x in rec["meta_campaigns"] if x)
        rec["meta_adsets"] = sorted(x for x in rec["meta_adsets"] if x)
        rec["meta_ad_ids"] = sorted(rec["meta_ad_ids"])
        # Strip always-null parse fields to shrink latest.json — the frontend
        # reads missing dims as "(untagged)" via `ad[dim] ?? "(untagged)"`.
        out.append({k: v for k, v in rec.items() if v is not None})
    return out


def load_mappings(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def main():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    out_dir = repo_root / "public" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    first_seen_path = repo_root / "data" / "first_seen.json"

    mappings = load_mappings(repo_root / "data" / "mappings.json")
    first_seen = load_first_seen(first_seen_path)
    print(f"first_seen sidecar: {len(first_seen)} known ads")

    csv_paths = [a for a in sys.argv[1:] if a != "--csv"]
    offline = "--csv" in sys.argv[1:]

    now = datetime.now(timezone.utc)
    end = now.date()
    start = end - timedelta(days=LOOKBACK_DAYS)

    window_label = ATTRIBUTION_WINDOW
    if offline:
        print(f"OFFLINE mode: parsing {len(csv_paths)} CSV export(s)")
        rows = rows_from_csv(csv_paths)
        shop_id = "csv-export"
    else:
        print(f"Fetching Triple Whale ad-level data: {start} → {end}")
        print(f"Pixel lens: {ATTRIBUTION_MODEL} / {ATTRIBUTION_WINDOW} · Channels: {CHANNELS}")
        print("Verifying API key…")
        verify_key()
        rows = None
        shop_id = None
        for candidate in SHOP_ID_CANDIDATES:
            print(f"Trying shop id {candidate!r}…")
            rows, lens_filtered = fetch_daily_rows(candidate, start, end)
            if rows:
                shop_id = candidate
                if not lens_filtered:
                    window_label = "lifetime (7_days filter unavailable)"
                break
        if not rows:
            sys.exit("ERROR: no shop id candidate returned data. Set TRIPLEWHALE_SHOP_ID.")

    print(f"  {len(rows)} daily rows")

    first_seen = update_first_seen(first_seen, rows)
    first_seen_path.parent.mkdir(parents=True, exist_ok=True)
    first_seen_path.write_text(json.dumps(first_seen, indent=0, sort_keys=True))
    print(f"first_seen sidecar now {len(first_seen)} ads → {first_seen_path}")

    print("Parsing & aggregating…")
    ads = aggregate_rows(rows, mappings=mappings, first_seen=first_seen)
    total_spend = sum(a["metrics"]["spend"] for a in ads)
    parsed_spend = sum(
        a["metrics"]["spend"] for a in ads if a.get("convention") not in (None, "unknown")
    )
    print(f"  {len(ads)} unique ads · ${total_spend:,.0f} spend · "
          f"{(parsed_spend / total_spend * 100) if total_spend else 0:.1f}% of spend parsed to a known family")

    manifest = {
        "generated_at": now.isoformat(),
        "period": {"start": f"{start}T00:00:00Z", "end": f"{end}T00:00:00Z"},
        "attribution": {
            "primary": "Meta channel-reported",
            "alternate": f"TW Pixel · {ATTRIBUTION_MODEL} · {window_label}",
            "model": ATTRIBUTION_MODEL,
            "window": window_label,
        },
        "platforms": ["Meta"],
        "shop_id": shop_id,
        "ad_count": len(ads),
        "ads": ads,
    }

    latest_path = out_dir / "latest.json"
    latest_path.write_text(json.dumps(manifest, separators=(",", ":"), default=str))
    print(f"Wrote {latest_path} ({latest_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
