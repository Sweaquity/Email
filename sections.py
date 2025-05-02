from datetime import datetime, timedelta
from utilities import (
    get_news, get_market_indices, get_market_sentiment,
    get_stock_movers, get_currency_rates, get_crypto,
    get_economic_calendar, format_economic_calendar_events,
    get_podcast_links
)


def generate_news_section():
    news_items = get_news()
    text = "\n".join(news_items)
    html = f"<h2>📰 Top Financial News</h2><pre>{text}</pre>"
    return html, text


def generate_market_indices_section():
    indices = get_market_indices()
    lines = []
    for d in indices.values():
        if d["current_price"] > 0:
            lines.append(f"{d['name']}: {d['current_price']} ({d['day']}% today, {d['week']}% week)")
    text = "\n".join(lines) or "Market indices data currently unavailable"
    html = f"<h2>📊 Market Indices</h2><pre>{text}</pre>"
    return html, text


def generate_market_sentiment_section():
    sentiment = get_market_sentiment()
    lines = [
    f"- {k}: {v['emoji']} {v['sentiment']} (RSI: {v['rsi']})" for k, v in sentiment.items()
    ]
    joined_lines = "\n".join(lines)
    text = "**Market Sentiment**\n" + joined_lines
    html = f"<h2>🔮 Market Sentiment</h2><pre>{joined_lines}</pre>"
    return html, text


def generate_market_movers_section():
    movers = get_stock_movers()
    def format_group(name, group):
        text = f"**{name}**\nGainers:\n" + "\n".join([
            f"- {s['ticker']}: {s['price']} ({s['change']}%)" for s in group["gainers"]
        ])
        text += "\nLosers:\n" + "\n".join([
            f"- {s['ticker']}: {s['price']} ({s['change']}%)" for s in group["losers"]
        ])
        return text

    grouped = {
        "UK Market Movers": {"gainers": movers["uk_gainers"], "losers": movers["uk_losers"]},
        "European Market Movers": {"gainers": movers["eu_gainers"], "losers": movers["eu_losers"]},
        "Australian Market Movers": {"gainers": movers["au_gainers"], "losers": movers["au_losers"]},
    }
    text = "\n\n".join([format_group(k, v) for k, v in grouped.items()])
    html = f"<h2>📈 Market Movers</h2><pre>{text}</pre>"
    return html, text


def generate_currency_and_crypto_section():
    rates = get_currency_rates()
    crypto = get_crypto()
    currency_text = "\n".join([
        f"- GBP/AUD: {rates.get('GBP/AUD', 'N/A')}",
        f"- EUR/GBP: {rates.get('EUR/GBP', 'N/A')}",
        f"- EUR/AUD: {rates.get('EUR/AUD', 'N/A')}"
    ])
    crypto_text = "\n".join([
        f"- {c}: £{d.get('GBP')} | €{d.get('EUR')} | A${d.get('AUD')}" for c, d in crypto.items()
    ])
    text = f"**Currency Rates**\n{currency_text}\n\n**Cryptocurrencies**\n{crypto_text}"
    html = f"<h2>💱 Currency & Crypto</h2><pre>{text}</pre>"
    return html, text


def generate_economic_calendar_section():
    events = get_economic_calendar()
    formatted = format_economic_calendar_events(events)
    html = f"<h2>📅 Economic Calendar</h2><pre>{formatted}</pre>"
    return html, formatted


def generate_podcast_section():
    podcasts = get_podcast_links()
    html = f"<h2>🎧 Financial Podcasts</h2><pre>{podcasts}</pre>"
    return html, podcasts
