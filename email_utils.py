import os
import smtplib
import pdfkit
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime
from utilities import CONFIG, logger


def generate_pdf(html_content):
    """Generate PDF from HTML content using wkhtmltopdf"""
    try:
        wkhtmltopdf_path = CONFIG.get('WKHTMLTOPDF_PATH', '')
        config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path) if wkhtmltopdf_path else None

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

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_file:
            if config:
                pdfkit.from_string(html_content, temp_file.name, configuration=config, options=options)
            else:
                pdfkit.from_string(html_content, temp_file.name, options=options)
            return temp_file.name
    except Exception as e:
        logger.error(f"Error generating PDF: {e}")
        return None


def send_email(content):
    """Send email with financial briefing content and optional PDF attachment"""
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = CONFIG['EMAIL_SENDER']
        msg['Subject'] = content['subject']

        # Attach plain and HTML versions
        msg.attach(MIMEText(content['text'], 'plain'))
        msg.attach(MIMEText(content['html'], 'html'))

        # Attach PDF
        pdf_path = generate_pdf(content['html'])
        if pdf_path:
            with open(pdf_path, 'rb') as f:
                part = MIMEApplication(f.read(), _subtype='pdf')
                part.add_header('Content-Disposition', 'attachment', filename=f"Financial_Briefing_{datetime.now().strftime('%Y%m%d')}.pdf")
                msg.attach(part)
            os.remove(pdf_path)

        # Send email to all recipients
        server = smtplib.SMTP(CONFIG['SMTP_SERVER'], CONFIG['SMTP_PORT'])
        server.starttls()
        server.login(CONFIG['EMAIL_SENDER'], CONFIG['EMAIL_PASSWORD'])

        for receiver in CONFIG['EMAIL_RECEIVERS']:
            msg['To'] = receiver
            server.sendmail(CONFIG['EMAIL_SENDER'], receiver, msg.as_string())
            logger.info(f"Email sent to {receiver}")

        server.quit()
        return True
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return False
