# utilities.py — clean module: only data fetching (no formatting)

import json
import os
import requests
import feedparser
import yfinance as yf
from datetime import datetime, timedelta
from functools import lru_cache
import logging
import time

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Config loader ---
def load_config():
    config_dir = os.path.join(os.path.expanduser("~"), "python apps", "Morning email")
    os.makedirs(config_dir, exist_ok=True)

    api_keys_path = os.path.join(config_dir, "api_keys.json")
    email_config_path = os.path.join(config_dir, "email_config.json")

    if not os.path.exists(api_keys_path):
        with open(api_keys_path, "w") as f:
            json.dump({"FMP_API_KEY": "your_fmp_key_here"}, f, indent=4)
        logger.info(f"Created API keys file at {api_keys_path}")

    if not os.path.exists(email_config_path):
        with open(email_config_path, "w") as f:
            json.dump({
                "SMTP_SERVER": "smtp.gmail.com",
                "SMTP_PORT": 587,
                "EMAIL_SENDER": "your_email@gmail.com",
                "EMAIL_PASSWORD": "your_password",
                "EMAIL_RECEIVERS": ["receiver@example.com"],
                "WKHTMLTOPDF_PATH": ""
            }, f, indent=4)
        logger.info(f"Created email config file at {email_config_path}")

    with open(api_keys_path) as f:
        api_keys = json.load(f)
    with open(email_config_path) as f:
        email_config = json.load(f)

    return {**api_keys, **email_config}

CONFIG = load_config()

# --- Helpers ---
@lru_cache(maxsize=32)
def make_api_request(url, retries=3, backoff=2):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"Attempt {attempt+1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(backoff ** attempt)
            else:
                logger.error(f"All retries failed for URL: {url}")
                return []

# --- News + Podcasts ---
def get_news():
    url = f"https://financialmodelingprep.com/api/v3/stock_news?limit=25&apikey={CONFIG['FMP_API_KEY']}"
    data = make_api_request(url)

    feeds = [
        ("Financial Times", "https://www.ft.com/rss/markets"),
        ("The Guardian", "https://www.theguardian.com/uk/business/rss"),
        ("AFR", "https://www.afr.com/rss/markets.xml")
    ]

    keywords = ['uk', 'britain', 'london', 'ftse', 'europe', 'euro', 'ecb', 'australia', 'asx', 'sydney']
    filtered = [
        f"- {a['title']} ({a['site']})" for a in data if any(k in a['title'].lower() for k in keywords)
    ][:10]

    for name, rss_url in feeds:
        feed = feedparser.parse(rss_url)
        filtered += [f"- {entry.title} ({name})" for entry in feed.entries[:3]]

    return filtered[:10]

def get_podcast_links():
    links = {
        "FT News Briefing": "https://rss.acast.com/ft-news-briefing",
        "BBC Global News Podcast": "https://podcasts.files.bbci.co.uk/p02nq0gn.rss",
        "The Economist - Money Talks": "https://open.spotify.com/show/2K9yxQj3zXF7Jv2O8R7Ego",
        "ABC Australia - The Business": "https://www.abc.net.au/radio/programs/the-business/",
        "Bloomberg UK - In the City": "https://www.bloomberg.com/uk/podcasts"
    }

    try:
        bbc = feedparser.parse(links["BBC Global News Podcast"])
        ft = feedparser.parse(links["FT News Briefing"])
        if bbc.entries:
            links["BBC Global News Podcast"] = bbc.entries[0].link
        if ft.entries:
            links["FT News Briefing"] = ft.entries[0].link
    except Exception as e:
        logger.warning(f"Podcast feed error: {e}")

    return links

# --- Markets ---
def get_market_indices():
    tickers = {
        "^FTSE": "FTSE 100 (UK)",
        "^GDAXI": "DAX (Germany)",
        "^FCHI": "CAC 40 (France)",
        "^STOXX50E": "Euro STOXX 50",
        "^AXJO": "ASX 200 (Australia)"
    }
    results = {}
    for ticker, name in tickers.items():
        try:
            hist = yf.Ticker(ticker).history(period="6mo")
            if not hist.empty:
                close = hist['Close']
                results[ticker] = {
                    "name": name,
                    "current_price": round(close.iloc[-1], 2),
                    "day": round((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100, 2),
                    "week": round((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100, 2)
                }
        except Exception as e:
            logger.error(f"Error fetching {ticker}: {e}")
    return results

def get_market_sentiment():
    symbols = {
        "UK": "^FTSE",
        "Germany": "^GDAXI",
        "France": "^FCHI",
        "Europe": "^STOXX50E",
        "Australia": "^AXJO"
    }
    sentiment = {}
    for name, sym in symbols.items():
        url = f"https://financialmodelingprep.com/api/v3/technical_indicator/daily/{sym}?period=10&type=rsi&apikey={CONFIG['FMP_API_KEY']}"
        data = make_api_request(url)
        rsi = data[0].get('rsi', 50) if data else 50
        if rsi > 70:
            sentiment[name] = {"rsi": rsi, "sentiment": "Very Bullish", "emoji": "🔥"}
        elif rsi > 60:
            sentiment[name] = {"rsi": rsi, "sentiment": "Bullish", "emoji": "📈"}
        elif rsi > 40:
            sentiment[name] = {"rsi": rsi, "sentiment": "Neutral", "emoji": "⚖️"}
        elif rsi > 30:
            sentiment[name] = {"rsi": rsi, "sentiment": "Bearish", "emoji": "📉"}
        else:
            sentiment[name] = {"rsi": rsi, "sentiment": "Very Bearish", "emoji": "🧊"}
    return sentiment

def get_currency_rates():
    pairs = ["GBPAUD", "EURGBP", "EURAUD"]
    results = {}
    for pair in pairs:
        url = f"https://financialmodelingprep.com/api/v3/quote/{pair}?apikey={CONFIG['FMP_API_KEY']}"
        data = make_api_request(url)
        if data:
            results[pair[:3] + "/" + pair[3:]] = round(data[0]['price'], 4)
    return results

def get_crypto():
    coins = ["BTC", "ETH", "XRP"]
    fiats = ["AUD", "GBP", "EUR"]
    result = {}
    for c in coins:
        result[c] = {}
        for f in fiats:
            url = f"https://financialmodelingprep.com/api/v3/quote/{c}{f}?apikey={CONFIG['FMP_API_KEY']}"
            data = make_api_request(url)
            if data:
                result[c][f] = round(data[0]['price'], 2)
    return result

def get_economic_calendar():
    today = datetime.now().strftime("%Y-%m-%d")
    week = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    url = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today}&to={week}&apikey={CONFIG['FMP_API_KEY']}"
    events = make_api_request(url)
    return [e for e in events if e.get('country') in ['AU', 'GB', 'EU', 'DE', 'FR', 'IT', 'ES', 'NL'] and e.get('impact') in ['High', 'Medium']]

def get_stock_movers():
    try:
        gainers_url = f"https://financialmodelingprep.com/api/v3/stock_market/gainers?apikey={CONFIG['FMP_API_KEY']}"
        losers_url = f"https://financialmodelingprep.com/api/v3/stock_market/losers?apikey={CONFIG['FMP_API_KEY']}"
        gainers = make_api_request(gainers_url)
        losers = make_api_request(losers_url)

        result = {
            "au_gainers": [], "au_losers": [],
            "uk_gainers": [], "uk_losers": [],
            "eu_gainers": [], "eu_losers": []
        }

        def match_region(entry, region):
            ex = entry.get("exchange", "").lower()
            sym = entry.get("symbol", "").lower()
            if region == 'au':
                return 'asx' in ex or '.au' in sym
            if region == 'uk':
                return 'lse' in ex or '.l' in sym
            if region == 'eu':
                return any(x in ex for x in ['frankfurt', 'paris', 'amsterdam']) or any(x in sym for x in ['.de', '.pa', '.as'])
            return False

        for region in ['au', 'uk', 'eu']:
            result[f"{region}_gainers"] = [
                {"ticker": g['symbol'], "price": g['price'], "change": g['changesPercentage']} for g in gainers if match_region(g, region)
            ][:4]
            result[f"{region}_losers"] = [
                {"ticker": l['symbol'], "price": l['price'], "change": l['changesPercentage']} for l in losers if match_region(l, region)
            ][:3]

        return result

        def format_economic_calendar_events(events):
            """Format economic events for display"""
            if not events:
                return "No significant economic events found."

            grouped = {}
            for e in events:
                grouped.setdefault(e['date'], []).append(e)

            countries = {
                'AU': '🇦🇺 Australia',
                'GB': '🇬🇧 UK',
                'EU': '🇪🇺 Eurozone',
                'DE': '🇩🇪 Germany',
                'FR': '🇫🇷 France',
                'IT': '🇮🇹 Italy',
                'ES': '🇪🇸 Spain',
                'NL': '🇳🇱 Netherlands'
            }

            lines = []
            for date_str, items in sorted(grouped.items()):
                d = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %d %B")
                lines.append(f"**{d}**")
                for e in items:
                    emoji = "🔴" if e['impact'] == "High" else "🟠"
                    name = countries.get(e['country'], e['country'])
                    desc = f"- {emoji} {name}: {e['event']}"
                    if e.get('forecast'):
                        desc += f" (Forecast: {e['forecast']}"
                        if e.get('previous'):
                            desc += f", Previous: {e['previous']})"
                        else:
                            desc += ")"
                    elif e.get('previous'):
                        desc += f" (Previous: {e['previous']})"
                    lines.append(desc)
                lines.append("")

            return "\n".join(lines)    

    except Exception as e:
        logger.error(f"Error fetching stock movers: {e}")
        return {
            "au_gainers": [], "au_losers": [],
            "uk_gainers": [], "uk_losers": [],
            "eu_gainers": [], "eu_losers": []
        }
