import logging
from email_utils import send_email
from sections import (
    generate_news_section,
    generate_market_indices_section,
    generate_market_sentiment_section,
    generate_market_movers_section,
    generate_currency_and_crypto_section,
    generate_economic_calendar_section,
    generate_podcast_section
)
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


def create_email():
    """Compile full email content from all sections"""
    current_date = datetime.now().strftime("%A, %d %B %Y")

    with ThreadPoolExecutor(max_workers=6) as executor:
        news_future = executor.submit(generate_news_section)
        indices_future = executor.submit(generate_market_indices_section)
        sentiment_future = executor.submit(generate_market_sentiment_section)
        movers_future = executor.submit(generate_market_movers_section)
        currency_crypto_future = executor.submit(generate_currency_and_crypto_section)
        econ_future = executor.submit(generate_economic_calendar_section)
        podcast_future = executor.submit(generate_podcast_section)

        news_html, news_text = news_future.result()
        indices_html, indices_text = indices_future.result()
        sentiment_html, sentiment_text = sentiment_future.result()
        movers_html, movers_text = movers_future.result()
        currency_crypto_html, currency_crypto_text = currency_crypto_future.result()
        econ_html, econ_text = econ_future.result()
        podcast_html, podcast_text = podcast_future.result()

    html_content = f"""
    <html>
    <body>
        <h1>Financial Morning Briefing - {current_date}</h1>
        {news_html}
        {indices_html}
        {sentiment_html}
        {movers_html}
        {currency_crypto_html}
        {econ_html}
        {podcast_html}
    </body>
    </html>
    """

    text_content = f"""
    Financial Morning Briefing - {current_date}

    {news_text}
    {indices_text}
    {sentiment_text}
    {movers_text}
    {currency_crypto_text}
    {econ_text}
    {podcast_text}
    """

    return {
        "html": html_content,
        "text": text_content,
        "subject": f"Financial Morning Briefing - {current_date}"
    }


def main():
    logger.info("Starting Morning Financial Briefing generation")
    content = create_email()
    if send_email(content):
        logger.info("Morning Financial Briefing email sent successfully!")
    else:
        logger.error("Failed to send Morning Financial Briefing email")


if __name__ == "__main__":
    main()
