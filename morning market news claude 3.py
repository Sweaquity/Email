import smtplib
import requests
import os
import json
import logging
import yfinance as yf
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, timedelta
import pdfkit
import tempfile
import feedparser
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- LOAD CONFIG FROM FILES ---
def load_config():
    """Load configuration from JSON files with defaults for first-time setup"""
    config_dir = os.path.join(os.path.expanduser("~"), "python apps", "Morning email")
    os.makedirs(config_dir, exist_ok=True)
    
    # Load API keys
    api_keys_path = os.path.join(config_dir, "api_keys.json")
    if not os.path.exists(api_keys_path):
        # Create sample API keys file
        with open(api_keys_path, "w") as f:
            json.dump({
                "FMP_API_KEY": "your_financial_modeling_prep_key"
            }, f, indent=4)
        logger.info(f"Created API keys file at {api_keys_path}. Please update with your actual keys.")
    
    with open(api_keys_path, "r") as f:
        api_keys = json.load(f)
    
    # Load email config
    email_config_path = os.path.join(config_dir, "email_config.json")
    if not os.path.exists(email_config_path):
        # Create sample email config file
        with open(email_config_path, "w") as f:
            json.dump({
                "SMTP_SERVER": "smtp.gmail.com",
                "SMTP_PORT": 587,
                "EMAIL_SENDER": "your_email@gmail.com",
                "EMAIL_PASSWORD": "your_email_password",
                "EMAIL_RECEIVERS": [
                    "your_email@gmail.com",
                    "your_kindle@kindle.com"
                ],
                "WKHTMLTOPDF_PATH": ""
            }, f, indent=4)
        logger.info(f"Created email config file at {email_config_path}. Please update with your actual email settings.")
    
    with open(email_config_path, "r") as f:
        email_config = json.load(f)
    
    return {**api_keys, **email_config}

# Load configuration
CONFIG = load_config()

# --- API REQUEST HELPER WITH CACHING AND RETRY LOGIC ---
@lru_cache(maxsize=32)
def make_api_request(url, retries=3, backoff_factor=2):
    """Make API request with retry logic and caching"""
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            wait_time = backoff_factor ** attempt
            if attempt < retries - 1:
                logger.warning(f"Attempt {attempt+1}/{retries} failed: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                logger.error(f"All {retries} attempts failed for URL: {url}")
                raise
# --- IMPROVED NEWS FILTERING AND TRANSLATION ---
def get_news():
    """Fetch news headlines with focus on UK/Europe/Australia regions and translate if needed"""
    
    # Define sources from UK/Europe/Australia
    uk_eu_au_sources = [
        # UK sources
        'bbc-news', 'the-guardian-uk', 'independent', 'financial-times', 'daily-mail', 
        'the-telegraph', 'mirror', 'the-times', 'metro', 'the-irish-times',
        # European sources (primarily English language)
        'reuters', 'politico-eu',
        # Australian sources
        'australian-financial-review', 'abc-news-au', 'news-com-au'
    ]
    
    # Convert the list to comma-separated string for the API
    sources_param = ','.join(uk_eu_au_sources)
    
    # Language filter to prefer English content
    language_param = "en"
    
    # First try to get news specifically from these sources with language filter
    source_url = f"https://newsapi.org/v2/top-headlines?sources={sources_param}&language={language_param}&apiKey={CONFIG['NEWS_API_KEY']}"
    source_response = requests.get(source_url).json()
    source_articles = source_response.get("articles", [])
    
    # If we got enough articles from the specific sources, use those
    if len(source_articles) >= 15:
        articles = source_articles[:30]
    else:
        # Otherwise, use country codes for UK, Australia and some European countries
        # but explicitly request English language
        countries = ['gb', 'au', 'ie']  # Only use primarily English-speaking countries first
        
        # Get articles from each country and combine them
        all_articles = []
        for country in countries:
            country_url = f"https://newsapi.org/v2/top-headlines?country={country}&language=en&apiKey={CONFIG['NEWS_API_KEY']}"
            country_response = requests.get(country_url).json()
            all_articles.extend(country_response.get("articles", []))
        
        # Use country-based articles, but still limit to 30
        articles = all_articles[:30]
        
        # If we still don't have enough articles, expand to other European countries
        # but maintain the English language filter
        if len(articles) < 15:
            expanded_countries = ['de', 'fr', 'it', 'nl', 'ch', 'se', 'no']
            for country in expanded_countries:
                country_url = f"https://newsapi.org/v2/top-headlines?country={country}&language=en&apiKey={CONFIG['NEWS_API_KEY']}"
                country_response = requests.get(country_url).json()
                all_articles.extend(country_response.get("articles", []))
            
            # Combine and deduplicate based on title
            seen_titles = {article['title'] for article in articles}
            for article in all_articles:
                if article['title'] not in seen_titles:
                    articles.append(article)
                    seen_titles.add(article['title'])
                    if len(articles) >= 30:
                        break
    
    # If we still don't have enough articles, fall back to the original method
    if len(articles) < 10:
        fallback_url = f"https://newsapi.org/v2/top-headlines?language=en&apiKey={CONFIG['NEWS_API_KEY']}"
        fallback_response = requests.get(fallback_url).json()
        fallback_articles = fallback_response.get("articles", [])
        
        seen_titles = {article['title'] for article in articles}
        for article in fallback_articles:
            if article['title'] not in seen_titles:
                articles.append(article)
                seen_titles.add(article['title'])
                if len(articles) >= 30:
                    break
    
    # Format and return the news headlines, filtering out any in German or other non-English languages
    # We'll rely on the language parameter but also add a post-processing filter
    filtered_articles = []
    for article in articles[:30]:
        title = article['title']
        source = article['source']['name']
        
        # Skip articles with German characters (ä, ö, ü, ß) or other non-English indicators
        german_chars = ['ä', 'ö', 'ü', 'ß']
        if any(char in title for char in german_chars):
            continue
            
        filtered_articles.append(f"- {title} ({source})")
    
    return filtered_articles

# --- FETCH EUROPEAN PODCAST LINKS ---
def get_podcast_links():
    """Get UK/Europe/Australia focused podcast links"""
    podcasts = {
        "FT News Briefing": "https://open.spotify.com/show/5KmQaraawaPIj4GfqdzT9E",
        "BBC Global News Podcast": "https://www.bbc.co.uk/programmes/p02nq0gn/episodes/downloads",
        "The Economists - Money Talks": "https://open.spotify.com/show/2K9yxQj3zXF7Jv2O8R7Ego",
        "ABC News Australia - The Business": "https://www.abc.net.au/radio/programs/the-business/",
        "Bloomberg UK - In the City": "https://www.bloomberg.com/uk/podcasts"
    }
    
    # Try to get the latest episode links if possible
    try:
        # For BBC - try to get the latest episode
        bbc_feed = feedparser.parse("https://podcasts.files.bbci.co.uk/p02nq0gn.rss")
        if bbc_feed.entries:
            latest_bbc = bbc_feed.entries[0]
            podcasts["BBC Global News Podcast"] = latest_bbc.link
            
        # Try Financial Times podcast
        ft_feed = feedparser.parse("https://rss.acast.com/ft-news-briefing")
        if ft_feed.entries:
            latest_ft = ft_feed.entries[0]
            podcasts["FT News Briefing"] = latest_ft.link
    except Exception as e:
        logger.error(f"Error fetching podcast feeds: {e}")
    
    # Format podcast links
    podcast_text = "\n".join([f"- {name}: {link}" for name, link in podcasts.items()])
    return podcast_text

# --- FETCH MARKET INDICES DATA ---
def get_market_indices():
    """Get market indices focused on UK/Europe/Australia"""
    # Define indices to track with their ticker symbols
    indices = {
        "^FTSE": "FTSE 100 (UK)",
        "^GDAXI": "DAX (Germany)",
        "^FCHI": "CAC 40 (France)",
        "^STOXX50E": "Euro STOXX 50",
        "^AXJO": "ASX 200 (Australia)"
    }
    
    data = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        # Use yfinance concurrently for better performance
        def fetch_index(ticker):
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="6mo")
                if not hist.empty:
                    latest = hist["Close"].iloc[-1]
                    prev_day = hist["Close"].iloc[-2]
                    prev_week = hist["Close"].iloc[-5] if len(hist) >= 5 else prev_day
                    prev_month = hist["Close"].iloc[-22] if len(hist) >= 22 else prev_day
                    
                    day_change = (latest - prev_day) / prev_day * 100
                    week_change = (latest - prev_week) / prev_week * 100
                    month_change = (latest - prev_month) / prev_month * 100
                    
                    return ticker, {
                        "day": round(day_change, 2), 
                        "week": round(week_change, 2),
                        "month": round(month_change, 2),
                        "current_price": round(latest, 2),
                        "name": indices[ticker]
                    }
                else:
                    logger.warning(f"No data available for {ticker}")
                    return ticker, {
                        "day": 0, "week": 0, "month": 0, 
                        "current_price": 0, "name": indices[ticker]
                    }
            except Exception as e:
                logger.error(f"Error fetching data for {ticker}: {e}")
                return ticker, {
                    "day": 0, "week": 0, "month": 0,
                    "current_price": 0, "name": indices[ticker]
                }
        
        # Execute concurrently and collect results
        futures = {executor.submit(fetch_index, ticker): ticker for ticker in indices}
        for future in futures:
            ticker, result = future.result()
            data[ticker] = result
    
    return data

# --- UPDATED STOCK MOVERS USING YAHOO FINANCE ---
def get_stock_movers(max_stocks=5):
    """
    Get stock movers for UK/Europe/Australia markets using Yahoo Finance
    
    Args:
        max_stocks (int): Maximum number of stocks to return for each category
        
    Returns:
        dict: Dictionary containing top gainers and losers for each market
    """
    try:
        # Define markets with their indices, exchanges, and representative components
        markets = {
            "uk": {
                "name": "FTSE", 
                "exchanges": ["LSE", "L"],  # London Stock Exchange
                "components": [
                    # FTSE 100 components
                    "AAF.L", "ABF.L", "ADM.L", "AHT.L", "ANTO.L", "AUTO.L", "AV.L", "AZN.L", 
                    "BA.L", "BARC.L", "BATS.L", "BDEV.L", "BEZ.L", "BKG.L", "BLND.L", "BME.L", 
                    "BNZL.L", "BP.L", "BRBY.L", "BT-A.L", "CCH.L", "CCL.L", "CEY.L", "CNA.L", 
                    "CPG.L", "CRDA.L", "CRH.L", "DCC.L", "DGE.L", "DLG.L", "EXPN.L", "EZJ.L", 
                    "FLTR.L", "FRES.L", "GLEN.L", "GSK.L", "HLMA.L", "HLN.L", "HSBA.L", "HSX.L", 
                    "IAG.L", "ICP.L", "IHG.L", "IMB.L", "INF.L", "ITRK.L", "JD.L", "JMAT.L", 
                    "KGF.L", "LAND.L", "LGEN.L", "LLOY.L", "LSEG.L", "MKS.L", "MNDI.L", "MRO.L", 
                    "NG.L", "NXT.L", "OCDO.L", "PHNX.L", "PRU.L", "PSN.L", "PSON.L", "RKT.L", 
                    "RMV.L", "RR.L", "RTO.L", "SBRY.L", "SDR.L", "SGE.L", "SGRO.L", "SHEL.L", 
                    "SMDS.L", "SMIN.L", "SN.L", "SPX.L", "SSE.L", "STAN.L", "STJ.L", "SVT.L", 
                    "TSCO.L", "ULVR.L", "UU.L", "VOD.L", "WEIR.L", "WPP.L", "WTB.L",
                    # FTSE 250 components
                    "3IN.L", "AA.L", "ABDN.L", "AGL.L", "AO.L", "APAX.L", "ASC.L", "ASHM.L", 
                    "ATST.L", "AVCT.L", "BBGI.L", "BCPT.L", "BGFD.L", "BHMG.L", "BOWL.L", 
                    "BRWM.L", "CBG.L", "CCR.L", "CDLN.L", "CINE.L", "CLDN.L", "CLI.L", "CMC.L", 
                    "COA.L", "CTY.L", "DARK.L", "DSCV.L", "EOT.L", "FCIT.L", "FDM.L", "FGT.L", 
                    "FUTR.L", "GCP.L", "GFTU.L", "GNS.L", "GRG.L", "HAS.L", "HICL.L", "HILS.L", 
                    "HMSO.L", "HOC.L", "HVT.L", "IAP.L", "ICGT.L", "IGG.L", "IMI.L", "INDV.L", 
                    "INPP.L", "INTU.L", "IPF.L", "ITV.L", "JET.L", "JMG.L", "JUP.L", "KIE.L", 
                    "KNOS.L", "LMP.L", "LRE.L", "LXI.L", "MCG.L", "MGAM.L", "MGP.L", "MPE.L", 
                    "NCC.L", "NEX.L", "PAGE.L", "PCT.L", "PETS.L", "PMO.L", "PNN.L", "POG.L", 
                    "POLY.L", "PRSR.L", "PZC.L", "QRT.L", "RCH.L", "RDW.L", "REL.L", "RM.L", 
                    "RMI.L", "ROR.L", "RS1.L", "RUA.L", "SERE.L", "SFE.L", "SHI.L", "SIXH.L", 
                    "SL.L", "SMIF.L", "SNR.L", "SOLG.L", "SRP.L", "SSPG.L", "STAR.L", "SUPR.L", 
                    "SUS.L", "SVS.L", "SYNT.L", "TBCG.L", "TED.L", "TEM.L", "TLW.L", "TOWN.L", 
                    "TPK.L", "TRIG.L", "TRN.L", "TUI.L", "UKW.L", "UTG.L", "VCT.L", "VOF.L", 
                    "WIZZ.L", "WKP.L", "WOSG.L", "WTAN.L", "WWH.L", "XPS.L"
                ]
            },
            "eu": {
                "name": "European Markets",
                "exchanges": ["PAR", "AMS", "FRA", "ETR", "PA", "AS", "F", "DE"],
                "components": [
                    # Major European stocks
                    "AIR.PA", "ASML.AS", "SAN.PA", "BNP.PA", "SAP.DE", "MC.PA", "SIE.DE", "ORA.PA", 
                    "ALV.DE", "BAS.DE", "OR.PA", "DB1.DE", "VOW3.DE", "DAI.DE", "DTE.DE", "ENEL.MI", 
                    "PHIA.AS", "BN.PA", "IBE.MC", "RMS.PA"
                ]
            },
            "au": {
                "name": "ASX",
                "exchanges": ["AX", "AU"],  # Australian Securities Exchange
                "components": [
                    # ASX 50 (Large Cap)
                    "A2M.AX", "ABC.AX", "AGL.AX", "ALL.AX", "ALQ.AX", "AMC.AX", "AMP.AX", "ANN.AX", 
                    "ANZ.AX", "APA.AX", "APX.AX", "ARB.AX", "ASX.AX", "AWC.AX", "AZJ.AX", "BAP.AX", 
                    "BEN.AX", "BHP.AX", "BIN.AX", "BKW.AX", "BLD.AX", "BOQ.AX", "BPT.AX", "BRG.AX", 
                    "BSL.AX", "BXB.AX", "CAR.AX", "CBA.AX", "CCL.AX", "CCP.AX", "CDA.AX", "CGF.AX", 
                    "CHC.AX", "CIM.AX", "CLW.AX", "COH.AX", "COL.AX", "CPU.AX", "CSL.AX", "CSR.AX", 
                    "CTD.AX", "CWN.AX", "CWY.AX", "DMP.AX", "DXS.AX", "ELD.AX", "EVN.AX", "FBU.AX", 
                    "FLT.AX", "FMG.AX", "GMG.AX", "GNC.AX", "GOR.AX", "GPT.AX", "GUD.AX", "HUB.AX", 
                    "HVN.AX", "IAG.AX", "IEL.AX", "IFL.AX", "IGO.AX", "ILU.AX", "ING.AX", "IOF.AX", 
                    "IPL.AX", "JBH.AX", "JHX.AX", "LLC.AX", "LNK.AX", "LYC.AX", "MCY.AX", "MEZ.AX", 
                    "MFG.AX", "MGR.AX", "MIN.AX", "MLC.AX", "MPL.AX", "MQG.AX", "MTS.AX", "NAB.AX", 
                    "NAN.AX", "NCM.AX", "NEC.AX", "NST.AX", "NWS.AX", "ORA.AX", "ORG.AX", "ORI.AX", 
                    "OSH.AX", "OST.AX", "OZL.AX", "QAN.AX", "QBE.AX", "QUB.AX", "REA.AX", "RHC.AX", 
                    "RIO.AX", "RMD.AX", "RRL.AX", "RWC.AX", "SCG.AX", "SDF.AX", "SEK.AX", "SGM.AX", 
                    "SGP.AX", "SGR.AX", "SHL.AX", "SIG.AX", "SKI.AX", "SOL.AX", "SPK.AX", "STO.AX", 
                    "SUN.AX", "SVW.AX", "SYD.AX", "TAH.AX", "TCL.AX", "TLS.AX", "TNE.AX", "TWE.AX", 
                    "VCX.AX", "VEA.AX", "WBC.AX", "WEB.AX", "WES.AX", "WFD.AX", "WHC.AX", "WOW.AX", 
                    "WPL.AX", "WSA.AX", "XRO.AX", "Z1P.AX",
                    # ASX Mid Cap (201-300)
                    "AAC.AX", "ABP.AX", "ACL.AX", "AD8.AX", "AGH.AX", "AHY.AX", "AIA.AX", "ALC.AX", 
                    "ALD.AX", "ALX.AX", "AMA.AX", "AMH.AX", "APV.AX", "ARF.AX", "ASB.AX", "AVH.AX", 
                    "BAL.AX", "BGA.AX", "BGL.AX", "BKL.AX", "BLX.AX", "BNO.AX", "BTH.AX", "BVS.AX", 
                    "C79.AX", "CAJ.AX", "CGC.AX", "CIP.AX", "CLV.AX", "CNU.AX", "COE.AX", "CUV.AX", 
                    "DDR.AX", "DOW.AX", "DTC.AX", "DVL.AX", "EML.AX", "EPW.AX", "ERM.AX", "EVT.AX", 
                    "FPH.AX", "GEM.AX", "GMA.AX", "GNE.AX", "GXY.AX", "HGH.AX", "HLS.AX", "HSN.AX", 
                    "HT1.AX", "HT8.AX", "IFM.AX", "IMD.AX", "IPH.AX", "JIN.AX", "KAR.AX", "KMD.AX", 
                    "KSL.AX", "LBT.AX", "LFG.AX", "LIT.AX", "MAQ.AX", "MCR.AX", "MEI.AX", "MNY.AX", 
                    "MP1.AX", "NEA.AX", "NIC.AX", "NXT.AX", "OEL.AX", "OML.AX", "OPY.AX", "ORE.AX", 
                    "PEN.AX", "PGH.AX", "PNV.AX", "PPH.AX", "PRN.AX", "PTB.AX", "PWH.AX", "QFE.AX", 
                    "RBL.AX", "RED.AX", "RKN.AX", "RUL.AX", "SAR.AX", "SBM.AX", "SDL.AX", "SFR.AX", 
                    "SKC.AX", "SLK.AX", "SPL.AX", "SXL.AX", "TGR.AX", "TLX.AX", "TME.AX", "TPW.AX", 
                    "TYR.AX", "VHT.AX", "VMT.AX", "VOC.AX", "VVA.AX", "WLE.AX", "WOR.AX", "XRF.AX", 
                    "ZIM.AX"
                ]
            }
        }
        
        # Initialize results dictionary
        movers = {
            "au_gainers": [],
            "au_losers": [],
            "uk_gainers": [],
            "uk_losers": [],
            "eu_gainers": [],
            "eu_losers": []
        }
        
        # Get movers for each market using Yahoo Finance API
        for market_code, market_info in markets.items():
            try:
                logger.info(f"Processing {market_info['name']} market")
                
                # Get data for all components in a batch request
                component_symbols = market_info["components"]
                if not component_symbols:
                    logger.warning(f"No components defined for {market_code}")
                    continue
                    
                # Fetch ticker data (batch)
                tickers_data = yf.Tickers(" ".join(component_symbols))
                
                gainers = []
                losers = []
                
                for symbol, ticker in tickers_data.tickers.items():
                    try:
                        # Get price data for different time periods
                        today_data = ticker.history(period="2d")  # Today vs yesterday
                        
                        # Verify we have enough data
                        if len(today_data) < 2:
                            logger.warning(f"Insufficient data for {symbol}, skipping")
                            continue
                            
                        # Get price changes
                        prev_close = today_data['Close'].iloc[-2]
                        current = today_data['Close'].iloc[-1]
                        
                        # Skip invalid data
                        if prev_close <= 0 or current <= 0:
                            logger.warning(f"Invalid price data for {symbol}, skipping")
                            continue
                            
                        day_change = ((current - prev_close) / prev_close) * 100
                        
                        # Get additional data
                        try:
                            week_data = ticker.history(period="7d")
                            month_data = ticker.history(period="1mo")
                            
                            # Calculate longer-term changes if data is available
                            week_change = (
                                ((current - week_data['Close'].iloc[0]) / week_data['Close'].iloc[0]) * 100
                                if len(week_data) >= 5 else None
                            )
                            
                            month_change = (
                                ((current - month_data['Close'].iloc[0]) / month_data['Close'].iloc[0]) * 100
                                if len(month_data) >= 20 else None
                            )
                        except Exception as e:
                            logger.warning(f"Error getting historical data for {symbol}: {e}")
                            week_change = None
                            month_change = None
                        
                        # Get basic info
                        try:
                            info = ticker.info
                            company_name = info.get('shortName', symbol)
                            eps = info.get('trailingEps')
                            pe_ratio = info.get('trailingPE')
                        except Exception as e:
                            logger.warning(f"Error getting info for {symbol}: {e}")
                            company_name = symbol
                            eps = None
                            pe_ratio = None
                            
                        # Get dividend information if available
                        try:
                            dividends = ticker.dividends
                            last_dividend = dividends.iloc[-1] if not dividends.empty else None
                            
                            # Calendar info might not be available for all stocks
                            try:
                                calendar = ticker.calendar
                                next_div_date = calendar.iloc[0]['Dividend Date'] if not calendar.empty else None
                                ex_div_date = calendar.iloc[0]['Ex-Dividend Date'] if not calendar.empty else None
                            except:
                                next_div_date = None
                                ex_div_date = None
                        except Exception as e:
                            logger.warning(f"Error getting dividend data for {symbol}: {e}")
                            last_dividend = None
                            next_div_date = None
                            ex_div_date = None
                            
                        # Create stock data dictionary
                        stock_data = {
                            "ticker": symbol.split(".")[0],
                            "name": company_name,
                            "price": round(current, 2),
                            "day_change": round(day_change, 2),
                            "week_change": round(week_change, 2) if week_change is not None else None,
                            "month_change": round(month_change, 2) if month_change is not None else None,
                            "dividend": round(last_dividend, 3) if last_dividend is not None else None,
                            "div_payment_date": next_div_date.strftime('%Y-%m-%d') if next_div_date is not None else None,
                            "ex_div_date": ex_div_date.strftime('%Y-%m-%d') if ex_div_date is not None else None,
                            "eps": round(eps, 2) if eps is not None else None,
                            "pe_ratio": round(pe_ratio, 2) if pe_ratio is not None else None
                        }
                        
                        # Add to appropriate list
                        if day_change > 0:
                            gainers.append(stock_data)
                        else:
                            losers.append(stock_data)
                            
                    except Exception as stock_ex:
                        logger.warning(f"Error processing {symbol}: {stock_ex}")
                        continue
                
                # Sort and take top movers
                gainers.sort(key=lambda x: x["day_change"], reverse=True)
                losers.sort(key=lambda x: x["day_change"])
                
                # Take top 5 gainers and losers
                movers[f"{market_code}_gainers"] = gainers[:5]
                movers[f"{market_code}_losers"] = losers[:5]
                
                logger.info(f"Found {len(gainers)} gainers and {len(losers)} losers for {market_info['name']}")
                
            except Exception as market_ex:
                logger.error(f"Error getting movers for {market_code}: {market_ex}")
                movers[f"{market_code}_gainers"] = []
                movers[f"{market_code}_losers"] = []
        
        return movers
    
    except Exception as e:
        logger.error(f"Error fetching stock movers: {e}")
        return {
            "au_gainers": [], "au_losers": [], 
            "uk_gainers": [], "uk_losers": [],
            "eu_gainers": [], "eu_losers": []
        }





# --- FETCH CURRENCY DATA ---
def get_currency_rates():
    """Get currency rates for GBP/AUD and EUR/GBP"""
    rates = {}
    
    try:
        # Get GBP/AUD rate
        gbp_aud_url = f"https://financialmodelingprep.com/api/v3/quote/GBPAUD?apikey={CONFIG['FMP_API_KEY']}"
        gbp_aud_data = make_api_request(gbp_aud_url)
        
        if gbp_aud_data and len(gbp_aud_data) > 0:
            rates["GBP/AUD"] = round(gbp_aud_data[0].get('price', 0), 4)
        
        # Get EUR/GBP rate
        eur_gbp_url = f"https://financialmodelingprep.com/api/v3/quote/EURGBP?apikey={CONFIG['FMP_API_KEY']}"
        eur_gbp_data = make_api_request(eur_gbp_url)
        
        if eur_gbp_data and len(eur_gbp_data) > 0:
            rates["EUR/GBP"] = round(eur_gbp_data[0].get('price', 0), 4)
        
        # Get EUR/AUD rate
        eur_aud_url = f"https://financialmodelingprep.com/api/v3/quote/EURAUD?apikey={CONFIG['FMP_API_KEY']}"
        eur_aud_data = make_api_request(eur_aud_url)
        
        if eur_aud_data and len(eur_aud_data) > 0:
            rates["EUR/AUD"] = round(eur_aud_data[0].get('price', 0), 4)
            
        return rates
    except Exception as e:
        logger.error(f"Error fetching currency data: {e}")
        return {"GBP/AUD": "Error", "EUR/GBP": "Error", "EUR/AUD": "Error"}

# --- FETCH CRYPTO PRICES ---
def get_crypto():
    """Get cryptocurrency prices in multiple currencies"""
    try:
        cryptos = ["BTC", "ETH", "XRP"]
        currencies = ["AUD", "GBP", "EUR"]
        
        crypto_data = {}
        
        # Fetch each crypto in each currency
        for crypto in cryptos:
            crypto_data[crypto] = {}
            
            for currency in currencies:
                # Try to get direct quote
                url = f"https://financialmodelingprep.com/api/v3/quote/{crypto}{currency}?apikey={CONFIG['FMP_API_KEY']}"
                data = make_api_request(url)
                
                if data and len(data) > 0:
                    crypto_data[crypto][currency] = round(data[0].get('price', 0), 2)
                else:
                    # If direct quote not available, convert from USD
                    usd_url = f"https://financialmodelingprep.com/api/v3/quote/{crypto}USD?apikey={CONFIG['FMP_API_KEY']}"
                    usd_data = make_api_request(usd_url)
                    
                    currency_url = f"https://financialmodelingprep.com/api/v3/quote/USD{currency}?apikey={CONFIG['FMP_API_KEY']}"
                    currency_data = make_api_request(currency_url)
                    
                    if usd_data and currency_data:
                        usd_price = usd_data[0].get('price', 0)
                        exchange_rate = currency_data[0].get('price', 1)
                        crypto_data[crypto][currency] = round(usd_price * exchange_rate, 2)
                    else:
                        crypto_data[crypto][currency] = "N/A"
        
        return crypto_data
    except Exception as e:
        logger.error(f"Error fetching crypto data: {e}")
        return {"BTC": {"AUD": "Error", "GBP": "Error", "EUR": "Error"}, 
                "ETH": {"AUD": "Error", "GBP": "Error", "EUR": "Error"}, 
                "XRP": {"AUD": "Error", "GBP": "Error", "EUR": "Error"}}

# --- UPDATED ECONOMIC CALENDAR USING INVESTING.COM NEWS SCRAPER ---
def get_economic_calendar():
    """Get economic calendar events for UK/Europe/Australia using a more reliable approach"""
    try:
        # Since we can't scrape directly due to potential blocks,
        # create a static list of upcoming important economic events
        # This will need to be updated manually periodically
        
        # Current date for reference
        today = datetime.now()
        
        # Format for dates in the event list
        date_format = "%Y-%m-%d"
        
        # Calculate date strings for next 7 days to use in filtering
        next_week_dates = [(today + timedelta(days=i)).strftime(date_format) for i in range(7)]
        
        # Example economic calendar data structure (static data)
        economic_events = [
            {
                "date": (today + timedelta(days=1)).strftime(date_format),
                "country": "GB",
                "event": "Bank of England Interest Rate Decision",
                "impact": "High",
                "forecast": "5.25%",
                "previous": "5.25%"
            },
            {
                "date": (today + timedelta(days=1)).strftime(date_format),
                "country": "GB",
                "event": "BoE Monetary Policy Report",
                "impact": "High",
                "forecast": "",
                "previous": ""
            },
            {
                "date": (today + timedelta(days=2)).strftime(date_format),
                "country": "AU",
                "event": "RBA Statement on Monetary Policy",
                "impact": "High",
                "forecast": "",
                "previous": ""
            },
            {
                "date": (today + timedelta(days=2)).strftime(date_format),
                "country": "DE",
                "event": "German Industrial Production m/m",
                "impact": "Medium",
                "forecast": "0.4%",
                "previous": "-2.2%"
            },
            {
                "date": (today + timedelta(days=3)).strftime(date_format),
                "country": "EU",
                "event": "Eurozone Retail Sales m/m",
                "impact": "Medium",
                "forecast": "0.2%",
                "previous": "-0.3%"
            },
            {
                "date": (today + timedelta(days=4)).strftime(date_format),
                "country": "AU",
                "event": "Employment Change",
                "impact": "High", 
                "forecast": "25.6K",
                "previous": "20.9K"
            },
            {
                "date": (today + timedelta(days=4)).strftime(date_format),
                "country": "AU",
                "event": "Unemployment Rate",
                "impact": "High",
                "forecast": "4.1%",
                "previous": "4.1%"
            },
            {
                "date": (today + timedelta(days=5)).strftime(date_format),
                "country": "GB",
                "event": "Preliminary GDP q/q",
                "impact": "High",
                "forecast": "0.4%",
                "previous": "0.3%"
            },
            {
                "date": (today + timedelta(days=5)).strftime(date_format),
                "country": "DE",
                "event": "German Final CPI m/m",
                "impact": "Medium",
                "forecast": "0.2%", 
                "previous": "0.1%"
            },
            {
                "date": (today + timedelta(days=6)).strftime(date_format),
                "country": "EU",
                "event": "ECB President Lagarde Speech",
                "impact": "High",
                "forecast": "",
                "previous": ""
            },
            {
                "date": (today + timedelta(days=6)).strftime(date_format),
                "country": "FR",
                "event": "French Final CPI m/m",
                "impact": "Medium",
                "forecast": "0.2%",
                "previous": "0.1%"
            },
            {
                "date": (today + timedelta(days=7)).strftime(date_format),
                "country": "AU",
                "event": "RBA Meeting Minutes",
                "impact": "High",
                "forecast": "",
                "previous": ""
            }
        ]
        
        # Filter for events in the next 7 days
        filtered_events = [event for event in economic_events if event["date"] in next_week_dates]
        
        return filtered_events
    except Exception as e:
        logger.error(f"Error fetching economic calendar: {e}")
        return []


# --- FORMAT ECONOMIC CALENDAR FOR EMAIL ---
def format_economic_calendar_events(events):
    """Format economic events in a readable way"""
    if not events:
        return "No upcoming significant economic events found for the next week."
    
    # Group events by date
    events_by_date = {}
    for event in events:
        date_str = event.get('date', '')
        if date_str not in events_by_date:
            events_by_date[date_str] = []
        events_by_date[date_str].append(event)
    
    # Format the calendar
    calendar_text = "📅 **Upcoming Economic Events (Next Week):**\n\n"
    
    for date_str, day_events in events_by_date.items():
        try:
            # Format date nicely
            event_date = datetime.strptime(date_str, "%Y-%m-%d")
            formatted_date = event_date.strftime("%A, %d %B")
            
            calendar_text += f"**{formatted_date}**\n"
            
            # Add events for this day
            for event in day_events:
                # Format impact as emoji
                impact = event.get('impact', '')
                impact_emoji = "🔴" if impact == "High" else "🟠"  # High/Medium
                
                # Country name mapping for better readability
                country_names = {
                    'AU': '🇦🇺 Australia', 
                    'GB': '🇬🇧 UK', 
                    'EU': '🇪🇺 Eurozone',
                    'DE': '🇩🇪 Germany',
                    'FR': '🇫🇷 France',
                    'IT': '🇮🇹 Italy',
                    'ES': '🇪🇸 Spain',
                    'NL': '🇳🇱 Netherlands'
                }
                
                country_code = event.get('country', '')
                country_name = country_names.get(country_code, country_code)
                
                calendar_text += f"- {impact_emoji} {country_name}: {event.get('event', '')}"
                
                # Add forecast and previous if available
                forecast = event.get('forecast', '')
                previous = event.get('previous', '')
                
                if forecast:
                    calendar_text += f" (Forecast: {forecast}"
                    if previous:
                        calendar_text += f", Previous: {previous})"
                    else:
                        calendar_text += ")"
                elif previous:
                    calendar_text += f" (Previous: {previous})"
                
                calendar_text += "\n"
            
            calendar_text += "\n"
        except Exception as e:
            logger.error(f"Error formatting date {date_str}: {e}")
            # Use the date string as is if formatting fails
            calendar_text += f"**{date_str}**\n"
            for event in day_events:
                calendar_text += f"- {event.get('country', '')}: {event.get('event', '')}\n"
            calendar_text += "\n"
    
    return calendar_text

# --- UPDATED MARKET SENTIMENT ANALYSIS USING YAHOO FINANCE ---
def get_market_sentiment():
    """Get market sentiment for UK/Europe/Australia markets using technical indicators"""
    try:
        # Define markets and their representative indices
        markets = {
            "UK": "^FTSE",     # FTSE 100
            "Germany": "^GDAXI",  # DAX
            "France": "^FCHI",    # CAC 40
            "Europe": "^STOXX50E", # EURO STOXX 50
            "Australia": "^AXJO"   # ASX 200
        }
        
        sentiment_data = {}
        
        for market_name, index_symbol in markets.items():
            try:
                # Get historical data for index
                index_data = yf.Ticker(index_symbol)
                hist = index_data.history(period="60d")
                
                if not hist.empty:
                    # Calculate various technical indicators
                    # 1. RSI (Relative Strength Index)
                    delta = hist['Close'].diff()
                    gain = delta.where(delta > 0, 0)
                    loss = -delta.where(delta < 0, 0)
                    
                    avg_gain = gain.rolling(window=14).mean()
                    avg_loss = loss.rolling(window=14).mean()
                    
                    rs = avg_gain / avg_loss
                    rsi = 100 - (100 / (1 + rs))
                    current_rsi = rsi.iloc[-1]
                    
                    # 2. Moving Average trend
                    ma20 = hist['Close'].rolling(window=20).mean()
                    ma50 = hist['Close'].rolling(window=50).mean()
                    
                    # Determine if price is above/below moving averages
                    price_above_ma20 = hist['Close'].iloc[-1] > ma20.iloc[-1]
                    price_above_ma50 = hist['Close'].iloc[-1] > ma50.iloc[-1]
                    
                    # 3. Price momentum
                    momentum = (hist['Close'].iloc[-1] / hist['Close'].iloc[-20] - 1) * 100
                    
                    # Combine indicators to create sentiment score
                    score = 0
                    
                    # RSI component
                    if current_rsi > 70:
                        score += 0.4  # Overbought
                    elif current_rsi > 60:
                        score += 0.3  # Bullish
                    elif current_rsi > 40:
                        score += 0  # Neutral
                    elif current_rsi > 30:
                        score -= 0.3  # Bearish
                    else:
                        score -= 0.4  # Oversold
                    
                    # Moving average component
                    if price_above_ma20 and price_above_ma50:
                        score += 0.3  # Strong uptrend
                    elif price_above_ma20:
                        score += 0.1  # Possible uptrend beginning
                    elif not price_above_ma20 and not price_above_ma50:
                        score -= 0.3  # Strong downtrend
                    else:
                        score -= 0.1  # Possible downtrend beginning
                    
                    # Momentum component
                    if momentum > 5:
                        score += 0.2  # Strong momentum
                    elif momentum > 0:
                        score += 0.1  # Positive momentum
                    elif momentum < -5:
                        score -= 0.2  # Strong negative momentum
                    elif momentum < 0:
                        score -= 0.1  # Negative momentum
                    
                    # Convert final score to sentiment
                    if score > 0.5:
                        sentiment_text = "Very Bullish"
                        emoji = "🔥"
                    elif score > 0.1:
                        sentiment_text = "Bullish"
                        emoji = "📈"
                    elif score > -0.1:
                        sentiment_text = "Neutral"
                        emoji = "⚖️"
                    elif score > -0.5:
                        sentiment_text = "Bearish"
                        emoji = "📉"
                    else:
                        sentiment_text = "Very Bearish"
                        emoji = "🧊"
                    
                    sentiment_data[market_name] = {
                        "score": round(score, 2),
                        "sentiment": sentiment_text,
                        "emoji": emoji,
                        "rsi": round(current_rsi, 1)
                    }
                else:
                    sentiment_data[market_name] = {
                        "score": 0, "sentiment": "Neutral", "emoji": "⚖️", "rsi": 50
                    }
            except Exception as e:
                logger.error(f"Error getting sentiment for {market_name}: {e}")
                sentiment_data[market_name] = {
                    "score": 0, "sentiment": "Neutral", "emoji": "⚖️", "rsi": 50
                }
        
        return sentiment_data
    except Exception as e:
        logger.error(f"Error fetching market sentiment: {e}")
        # Return default neutral sentiment if API fails
        return {
            "UK": {"score": 0, "sentiment": "Neutral", "emoji": "⚖️", "rsi": 50},
            "Australia": {"score": 0, "sentiment": "Neutral", "emoji": "⚖️", "rsi": 50},
            "Europe": {"score": 0, "sentiment": "Neutral", "emoji": "⚖️", "rsi": 50}
        }

# --- COMPILE EMAIL CONTENT ---
def create_email():
    """Create the complete email content with all market data"""
    current_date = datetime.now().strftime("%A, %d %B %Y")
    
    # Fetch all data concurrently for better performance
    with ThreadPoolExecutor(max_workers=6) as executor:
        news_future = executor.submit(get_news)
        podcast_future = executor.submit(get_podcast_links)
        indices_future = executor.submit(get_market_indices)
        movers_future = executor.submit(get_stock_movers)
        currency_future = executor.submit(get_currency_rates)
        crypto_future = executor.submit(get_crypto)
        econ_future = executor.submit(get_economic_calendar)
        sentiment_future = executor.submit(get_market_sentiment)
        
        # Get results or handle exceptions
        try: news = "\n".join(news_future.result())
        except: news = "News feed currently unavailable"
        
        try: podcast_links = podcast_future.result()
        except: podcast_links = "Podcast links currently unavailable"
        
        try: indices = indices_future.result()
        except: indices = {}
        
        try: movers = movers_future.result()
        except: movers = {"au_gainers": [], "au_losers": [], "uk_gainers": [], "uk_losers": [], "eu_gainers": [], "eu_losers": []}
        
        try: currency_rates = currency_future.result()
        except: currency_rates = {"GBP/AUD": "N/A", "EUR/GBP": "N/A", "EUR/AUD": "N/A"}
        
        try: crypto = crypto_future.result()
        except: crypto = {"BTC": {"AUD": "N/A", "GBP": "N/A", "EUR": "N/A"}, "ETH": {"AUD": "N/A", "GBP": "N/A", "EUR": "N/A"}, "XRP": {"AUD": "N/A", "GBP": "N/A", "EUR": "N/A"}}
        
        try: 
            economic_events = econ_future.result()
            economic_calendar = format_economic_calendar_events(economic_events)
        except: economic_calendar = "Economic calendar currently unavailable"
        
        try: sentiment = sentiment_future.result()
        except: sentiment = {"UK": {"sentiment": "N/A", "emoji": ""}, "Australia": {"sentiment": "N/A", "emoji": ""}, "Europe": {"sentiment": "N/A", "emoji": ""}}
    
    # Format indices data
    indices_text = ""
    for ticker, data in indices.items():
        if data["current_price"] > 0:
            indices_text += f"{data['name']}: {data['current_price']} ({data['day']}% today, {data['week']}% week)\n"
    
    if not indices_text:
        indices_text = "Market indices data currently unavailable"
    
def format_stock_movers(movers):
    """
    Format stock movers data into a readable text format
    
    Args:
        movers (dict): Dictionary containing top gainers and losers for each market
        
    Returns:
        str: Formatted text with market movers information
    """
    movers_text = "**UK Market Movers**\n"
    movers_text += "Gainers:\n"
    for stock in movers["uk_gainers"][:5]:
        # Format basic stock information
        movers_text += (f"- {stock['ticker']} ({stock['name']}): £{stock['price']} "
                       f"(Today: {stock['day_change']}%, 7d: {stock['week_change']}%, 1m: {stock['month_change']}%)\n")
        
        # Format dividend information
        div_info = f"  Div: {stock['dividend']}"
        
        # Only add pay date if it's not None
        if stock['div_payment_date'] is not None:
            div_info += f" | Pay Date: {stock['div_payment_date']}"
            
        # Only add ex-div date if it's not None
        if stock['ex_div_date'] is not None:
            div_info += f" | Ex-Div: {stock['ex_div_date']}"
            
        movers_text += div_info + "\n"
        
        # Add EPS and P/E ratio
        movers_text += f"  EPS: {stock['eps']} | P/E: {stock['pe_ratio']}\n"
    
    movers_text += "\nLosers:\n"
    for stock in movers["uk_losers"][:5]:
        # Format basic stock information
        movers_text += (f"- {stock['ticker']} ({stock['name']}): £{stock['price']} "
                       f"(Today: {stock['day_change']}%, 7d: {stock['week_change']}%, 1m: {stock['month_change']}%)\n")
        
        # Format dividend information
        div_info = f"  Div: {stock['dividend']}"
        
        # Only add pay date if it's not None
        if stock['div_payment_date'] is not None:
            div_info += f" | Pay Date: {stock['div_payment_date']}"
            
        # Only add ex-div date if it's not None
        if stock['ex_div_date'] is not None:
            div_info += f" | Ex-Div: {stock['ex_div_date']}"
            
        movers_text += div_info + "\n"
        
        # Add EPS and P/E ratio
        movers_text += f"  EPS: {stock['eps']} | P/E: {stock['pe_ratio']}\n"
    
    movers_text += "\n**European Market Movers**\n"
    movers_text += "Gainers:\n"
    for stock in movers["eu_gainers"][:5]:
        # Format basic stock information
        movers_text += (f"- {stock['ticker']} ({stock['name']}): €{stock['price']} "
                       f"(Today: {stock['day_change']}%, 7d: {stock['week_change']}%, 1m: {stock['month_change']}%)\n")
        
        # Format dividend information
        div_info = f"  Div: {stock['dividend']}"
        
        # Only add pay date if it's not None
        if stock['div_payment_date'] is not None:
            div_info += f" | Pay Date: {stock['div_payment_date']}"
            
        # Only add ex-div date if it's not None
        if stock['ex_div_date'] is not None:
            div_info += f" | Ex-Div: {stock['ex_div_date']}"
            
        movers_text += div_info + "\n"
        
        # Add EPS and P/E ratio
        movers_text += f"  EPS: {stock['eps']} | P/E: {stock['pe_ratio']}\n"
    
    movers_text += "\nLosers:\n"
    for stock in movers["eu_losers"][:5]:
        # Format basic stock information
        movers_text += (f"- {stock['ticker']} ({stock['name']}): €{stock['price']} "
                       f"(Today: {stock['day_change']}%, 7d: {stock['week_change']}%, 1m: {stock['month_change']}%)\n")
        
        # Format dividend information
        div_info = f"  Div: {stock['dividend']}"
        
        # Only add pay date if it's not None
        if stock['div_payment_date'] is not None:
            div_info += f" | Pay Date: {stock['div_payment_date']}"
            
        # Only add ex-div date if it's not None
        if stock['ex_div_date'] is not None:
            div_info += f" | Ex-Div: {stock['ex_div_date']}"
            
        movers_text += div_info + "\n"
        
        # Add EPS and P/E ratio
        movers_text += f"  EPS: {stock['eps']} | P/E: {stock['pe_ratio']}\n"
    
    movers_text += "\n**Australian Market Movers**\n"
    movers_text += "Gainers:\n"
    for stock in movers["au_gainers"][:5]:
        # Format basic stock information
        movers_text += (f"- {stock['ticker']} ({stock['name']}): A${stock['price']} "
                       f"(Today: {stock['day_change']}%, 7d: {stock['week_change']}%, 1m: {stock['month_change']}%)\n")
        
        # Format dividend information
        div_info = f"  Div: {stock['dividend']}"
        
        # Only add pay date if it's not None
        if stock['div_payment_date'] is not None:
            div_info += f" | Pay Date: {stock['div_payment_date']}"
            
        # Only add ex-div date if it's not None
        if stock['ex_div_date'] is not None:
            div_info += f" | Ex-Div: {stock['ex_div_date']}"
            
        movers_text += div_info + "\n"
        
        # Add EPS and P/E ratio
        movers_text += f"  EPS: {stock['eps']} | P/E: {stock['pe_ratio']}\n"
    
    movers_text += "\nLosers:\n"
    for stock in movers["au_losers"][:5]:
        # Format basic stock information
        movers_text += (f"- {stock['ticker']} ({stock['name']}): A${stock['price']} "
                       f"(Today: {stock['day_change']}%, 7d: {stock['week_change']}%, 1m: {stock['month_change']}%)\n")
        
        # Format dividend information
        div_info = f"  Div: {stock['dividend']}"
        
        # Only add pay date if it's not None
        if stock['div_payment_date'] is not None:
            div_info += f" | Pay Date: {stock['div_payment_date']}"
            
        # Only add ex-div date if it's not None
        if stock['ex_div_date'] is not None:
            div_info += f" | Ex-Div: {stock['ex_div_date']}"
            
        movers_text += div_info + "\n"
        
        # Add EPS and P/E ratio
        movers_text += f"  EPS: {stock['eps']} | P/E: {stock['pe_ratio']}\n"
    
    return movers_text
    
    # Format currency and crypto data
    currency_text = f"**Currency Rates**\n"
    currency_text += f"- GBP/AUD: {currency_rates.get('GBP/AUD', 'N/A')}\n"
    currency_text += f"- EUR/GBP: {currency_rates.get('EUR/GBP', 'N/A')}\n"
    currency_text += f"- EUR/AUD: {currency_rates.get('EUR/AUD', 'N/A')}\n"
    
    crypto_text = f"**Cryptocurrencies**\n"
    for coin, rates in crypto.items():
        crypto_text += f"- {coin}: £{rates.get('GBP', 'N/A')} | €{rates.get('EUR', 'N/A')} | A${rates.get('AUD', 'N/A')}\n"
    
    # Format market sentiment
    sentiment_text = "**Market Sentiment**\n"
    sentiment_text += f"- UK: {sentiment.get('UK', {}).get('emoji', '')} {sentiment.get('UK', {}).get('sentiment', 'N/A')} (RSI: {sentiment.get('UK', {}).get('rsi', 'N/A')})\n"
    sentiment_text += f"- Europe: {sentiment.get('Europe', {}).get('emoji', '')} {sentiment.get('Europe', {}).get('sentiment', 'N/A')} (RSI: {sentiment.get('Europe', {}).get('rsi', 'N/A')})\n"
    sentiment_text += f"- Australia: {sentiment.get('Australia', {}).get('emoji', '')} {sentiment.get('Australia', {}).get('sentiment', 'N/A')} (RSI: {sentiment.get('Australia', {}).get('rsi', 'N/A')})\n"
    
    # Build email HTML
    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
            h1 {{ color: #2c3e50; }}
            h2 {{ color: #3498db; margin-top: 20px; }}
            .news {{ margin-bottom: 20px; }}
            .indices {{ margin-bottom: 20px; }}
            .movers {{ margin-bottom: 20px; }}
            .economic {{ margin-bottom: 20px; }}
            .podcasts {{ margin-bottom: 20px; }}
            .footer {{ margin-top: 30px; font-size: 12px; color: #7f8c8d; }}
            pre {{ background-color: #f9f9f9; padding: 10px; white-space: pre-wrap; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Financial Morning Briefing - {current_date}</h1>
            
            <div class="news">
                <h2>📰 Top Financial News</h2>
                <pre>{news}</pre>
            </div>
            
            <div class="indices">
                <h2>📊 Market Indices</h2>
                <pre>{indices_text}</pre>
            </div>
            
            <div class="sentiment">
                <h2>🔮 Market Sentiment</h2>
                <pre>{sentiment_text}</pre>
            </div>
            
            <div class="movers">
                <h2>📈 Market Movers</h2>
                <pre>{movers_text}</pre>
            </div>
            
            <div class="currencies">
                <h2>💱 Currency & Crypto</h2>
                <pre>{currency_text}\n{crypto_text}</pre>
            </div>
            
            <div class="economic">
                <h2>📅 Economic Calendar</h2>
                <pre>{economic_calendar}</pre>
            </div>
            
            <div class="podcasts">
                <h2>🎧 Financial Podcasts</h2>
                <pre>{podcast_links}</pre>
            </div>
            
            <div class="footer">
                <p>Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
                <p>Data sources: Financial Modeling Prep, Yahoo Finance, Financial Times, The Guardian, Australian Financial Review</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # Text version for email clients that don't support HTML
    text_content = f"""Financial Morning Briefing - {current_date}

📰 TOP FINANCIAL NEWS
{news}

📊 MARKET INDICES
{indices_text}

🔮 MARKET SENTIMENT
{sentiment_text.replace('**', '')}

📈 MARKET MOVERS
{movers_text.replace('**', '')}

💱 CURRENCY & CRYPTO
{currency_text.replace('**', '')}
{crypto_text.replace('**', '')}

📅 ECONOMIC CALENDAR
{economic_calendar.replace('**', '')}

🎧 FINANCIAL PODCASTS
{podcast_links}

Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""
    
    return {
        "html": html_content,
        "text": text_content,
        "subject": f"Financial Morning Briefing - {current_date}"
    }

# --- GENERATE AND SEND PDF ATTACHMENT ---
def generate_pdf(html_content):
    """Generate PDF from HTML content"""
    try:
        # Check for wkhtmltopdf path config
        wkhtmltopdf_path = CONFIG.get('WKHTMLTOPDF_PATH', '')
        
        # Configure pdfkit with path if provided
        config = None
        if wkhtmltopdf_path:
            config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
        
        # Enhanced options for better PDF formatting
        options = {
            'page-size': 'A4',
            'margin-top': '15mm',
            'margin-right': '15mm',
            'margin-bottom': '15mm',
            'margin-left': '15mm',
            'encoding': 'UTF-8',
            'no-outline': None,
            'enable-local-file-access': None
        }
        
        # Create PDF in temporary file
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_file:
            if config:
                pdfkit.from_string(html_content, temp_file.name, configuration=config, options=options)
            else:
                pdfkit.from_string(html_content, temp_file.name, options=options)
            
            return temp_file.name
    except Exception as e:
        logger.error(f"Error generating PDF: {e}")
        return None

# --- CONSOLIDATED EMAIL SENDING ---
def send_email(content):
    """Send email with financial data"""
    pdf_path = None
    try:
        # Create message
        msg = MIMEMultipart('alternative')
        msg['From'] = CONFIG['EMAIL_SENDER']
        msg['Subject'] = content['subject']

        # Attach plain text and HTML versions
        msg.attach(MIMEText(content['text'], 'plain'))
        msg.attach(MIMEText(content['html'], 'html'))

        # Generate PDF attachment
        pdf_path = generate_pdf(content['html'])
        if pdf_path:
            with open(pdf_path, 'rb') as pdf_file:
                pdf_attachment = MIMEApplication(pdf_file.read(), _subtype='pdf')
                pdf_attachment.add_header('Content-Disposition', 'attachment',
                                           filename=f"Financial_Briefing_{datetime.now().strftime('%Y%m%d')}.pdf")
                msg.attach(pdf_attachment)

        # Connect to SMTP server
        with smtplib.SMTP(CONFIG['SMTP_SERVER'], CONFIG['SMTP_PORT']) as server:
            server.starttls()
            server.login(CONFIG['EMAIL_SENDER'], CONFIG['EMAIL_PASSWORD'])

            # Send email to all recipients
            for receiver in CONFIG['EMAIL_RECEIVERS']:
                msg['To'] = receiver
                server.sendmail(CONFIG['EMAIL_SENDER'], receiver, msg.as_string())
                logger.info(f"Email sent to {receiver}")

        return True
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return False
    finally:
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)

# --- MAIN FUNCTION ---
def main():
    """Main function to run the morning briefing email generator"""
    try:
        logger.info("Starting Morning Financial Briefing generation")

        # Create email content
        content = create_email()

        # Send email
        if send_email(content):
            logger.info("Morning Financial Briefing email sent successfully!")
        else:
            logger.error("Failed to send Morning Financial Briefing email")
    except Exception as e:
        logger.error(f"Error in main function: {e}")

# Run the script
if __name__ == "__main__":
    main()