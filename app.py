"""
Cryptocurrency Price Tracker — Backend
----------------------------------------
Serves scraped crypto data, provides real-time statistics,
currency conversion rates, and on-demand live scraping.
"""

import argparse
import csv
import json
import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_file, Response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LATEST_JSON = os.path.join(DATA_DIR, "latest.json")
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")
SCRAPE_INTERVAL = 300  # seconds

app = Flask(__name__, static_folder="static", template_folder="templates")

# Live conversion rates against USD
CURRENCY_RATES = {
    "USD": {"symbol": "$", "rate": 1.0, "name": "US Dollar"},
    "INR": {"symbol": "₹", "rate": 86.85, "name": "Indian Rupee"},
    "EUR": {"symbol": "€", "rate": 0.945, "name": "Euro"},
    "GBP": {"symbol": "£", "rate": 0.792, "name": "British Pound"},
    "AED": {"symbol": "AED", "rate": 3.673, "name": "UAE Dirham"},
}


def load_latest():
    if not os.path.isfile(LATEST_JSON):
        return {"updated_at": None, "coins": []}
    try:
        with open(LATEST_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"updated_at": None, "coins": []}


def market_pulse(coins):
    """
    Market-cap-weighted average of 24h change, scaled 0-100 gauge.
    >55 = bullish tilt, <45 = bearish tilt.
    """
    total_cap = sum(c.get("market_cap") or 0 for c in coins)
    if not total_cap:
        return 50.0
    weighted = sum((c.get("change_24h") or 0) * (c.get("market_cap") or 0) for c in coins)
    avg_change = weighted / total_cap
    score = 50 + (avg_change * 4)
    return max(0.0, min(100.0, round(score, 1)))


@app.route("/")
def index():
    return app.send_static_file("index.html") if os.path.isfile(
        os.path.join(app.static_folder, "index.html")
    ) else send_file(os.path.join(BASE_DIR, "templates", "index.html"))


@app.route("/api/coins")
def api_coins():
    data = load_latest()
    coins = data.get("coins", [])

    q = request.args.get("q", "").strip().lower()
    sort_by = request.args.get("sort", "rank")
    order = request.args.get("order", "asc")
    category = request.args.get("category", "all")  # all, gainers, losers

    if q:
        coins = [c for c in coins if q in c["name"].lower() or q in c["symbol"].lower()]

    if category == "gainers":
        coins = [c for c in coins if (c.get("change_24h") or 0) > 0]
    elif category == "losers":
        coins = [c for c in coins if (c.get("change_24h") or 0) < 0]

    valid_sort_fields = {"rank", "price", "change_1h", "change_24h", "change_7d", "market_cap", "volume_24h"}
    if sort_by in valid_sort_fields:
        coins = sorted(
            coins,
            key=lambda c: (c.get(sort_by) is None, c.get(sort_by)),
            reverse=(order == "desc")
        )

    return jsonify({
        "updated_at": data.get("updated_at"),
        "count": len(coins),
        "market_pulse": market_pulse(data.get("coins", [])),
        "coins": coins,
    })


@app.route("/api/highlights")
def api_highlights():
    """Returns top gainer, top loser, total market cap, and pulse."""
    data = load_latest()
    coins = data.get("coins", [])
    if not coins:
        return jsonify({"top_gainer": None, "top_loser": None, "total_market_cap": 0, "market_pulse": 50})

    sorted_by_change = sorted(coins, key=lambda c: c.get("change_24h") or 0)
    top_loser = sorted_by_change[0]
    top_gainer = sorted_by_change[-1]
    total_market_cap = sum(c.get("market_cap") or 0 for c in coins)
    total_volume = sum(c.get("volume_24h") or 0 for c in coins)

    return jsonify({
        "top_gainer": top_gainer,
        "top_loser": top_loser,
        "total_market_cap": total_market_cap,
        "total_volume_24h": total_volume,
        "market_pulse": market_pulse(coins),
        "coin_count": len(coins),
    })


@app.route("/api/rates")
def api_rates():
    """Returns currency conversion exchange rates."""
    return jsonify(CURRENCY_RATES)


@app.route("/api/coin/<symbol>/history")
def api_coin_history(symbol):
    """Returns historical price points for sparkline charts."""
    symbol = symbol.upper()
    points = []
    if os.path.isfile(HISTORY_CSV):
        with open(HISTORY_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("symbol", "").upper() == symbol:
                    try:
                        points.append({
                            "t": row.get("timestamp"),
                            "price": float(row.get("price") or 0)
                        })
                    except ValueError:
                        continue
    return jsonify(points[-100:])


@app.route("/api/scrape-now", methods=["GET", "POST"])
def api_scrape_now():
    """Triggers an immediate live web scrape and returns fresh data."""
    try:
        from scraper import run_once
        res = run_once(top_n=20, headless=True)
        data = load_latest()
        return jsonify({
            "status": "success",
            "message": "Live crypto data updated successfully!",
            "updated_at": data.get("updated_at"),
            "coin_count": len(data.get("coins", [])),
        })
    except Exception as exc:
        return jsonify({
            "status": "error",
            "message": f"Scrape failed: {str(exc)}"
        }), 500


@app.route("/api/export.csv")
def export_csv():
    if not os.path.isfile(HISTORY_CSV):
        return Response("No data yet — run the scraper first.", mimetype="text/plain")
    return send_file(HISTORY_CSV, as_attachment=True, download_name="crypto_history.csv")


@app.route("/api/status")
def api_status():
    data = load_latest()
    return jsonify({
        "updated_at": data.get("updated_at"),
        "coin_count": len(data.get("coins", [])),
        "has_history": os.path.isfile(HISTORY_CSV),
        "currencies_supported": list(CURRENCY_RATES.keys()),
    })


def background_scraper(interval: int):
    from scraper import run_once
    while True:
        try:
            print(f"[auto-scrape] running… ({datetime.now(timezone.utc).isoformat(timespec='seconds')})")
            run_once(top_n=20, headless=True)
        except Exception as exc:
            print(f"[auto-scrape] failed: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-scrape", action="store_true", help="Re-scrape on a background timer.")
    parser.add_argument("--interval", type=int, default=SCRAPE_INTERVAL)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug/auto-reload.")
    args = parser.parse_args()

    if args.auto_scrape:
        t = threading.Thread(target=background_scraper, args=(args.interval,), daemon=True)
        t.start()

    app.run(debug=args.debug, host=args.host, port=args.port)
