"""Durable staff notifications; requires an authorized email sender before delivery."""
import json, os, threading, time, base64, html
from email.message import EmailMessage
import requests
from sqlalchemy import Table, Column, String, Text, Integer, select, insert, update

RECIPIENTS = ['alec@hookedonsolar.com', 'customerservice@hookedonsolar.com']
_lock = threading.Lock()

def define_table(meta):
    return Table('care_email_outbox', meta,
        Column('id', String, primary_key=True), Column('subject', String),
        Column('body', Text), Column('reply_to', String), Column('recipients', Text),
        Column('state', String, nullable=False), Column('first_attempt', Integer),
        Column('provider_id', String))

def enqueue(conn, table, event_id, subject, body, reply_to):
    conn.execute(insert(table).values(id=event_id, subject=subject, body=body,
        reply_to=reply_to, recipients=json.dumps(RECIPIENTS), state='pending'))

def gmail_configured():
    return all(os.getenv(k) for k in ('GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','GOOGLE_REFRESH_TOKEN'))

def configured():
    return gmail_configured() or bool(os.getenv('RESEND_API_KEY') and os.getenv('NOTIFICATION_FROM'))

def deliver_gmail(engine, table, rows, logger):
    # Only send permission is required; this integration does not read mail.
    token = requests.post('https://oauth2.googleapis.com/token', data={
        'client_id': os.environ['GOOGLE_CLIENT_ID'],
        'client_secret': os.environ['GOOGLE_CLIENT_SECRET'],
        'refresh_token': os.environ['GOOGLE_REFRESH_TOKEN'],
        'grant_type': 'refresh_token'}, timeout=15)
    token.raise_for_status()
    access_token = token.json()['access_token']
    for row in rows:
        recipients = [email for email in json.loads(row['recipients']) if email in RECIPIENTS]
        if not recipients:
            continue
        msg = EmailMessage()
        msg['From'] = 'customerservice@hookedonsolar.com'
        msg['To'] = ', '.join(recipients)
        msg['Reply-To'] = row['reply_to']
        msg['Subject'] = row['subject']
        msg['Message-ID'] = '<' + row['id'] + '@hookedonsolar.com>'
        msg.set_content(row['body'])
        if row['subject'].startswith('Quality Assurance Form | '):
            form_html='<html><body><h1>Hooked on Solar — Quality Assurance Form</h1><table style="border-collapse:collapse">'+''.join('<tr><th style="text-align:left;padding:8px;border:1px solid #ddd">'+html.escape(line.partition(':')[0])+'</th><td style="padding:8px;border:1px solid #ddd;white-space:pre-wrap">'+html.escape(line.partition(':')[2])+'</td></tr>' for line in row['body'].splitlines())+'</table></body></html>'
            msg.add_alternative(form_html, subtype='html')
            msg.add_attachment(form_html.encode(),maintype='text',subtype='html',filename='Quality-Assurance-Form.html')
        # Atomic claim across processes. Gmail does not support idempotent sends;
        # uncertain outcomes remain held for review instead of blind retries.
        with engine.begin() as conn:
            claimed = conn.execute(update(table).where(table.c.id == row['id'], table.c.state == 'pending').values(state='sending', first_attempt=int(time.time())))
        if claimed.rowcount != 1:
            continue
        try:
            response = requests.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                headers={'Authorization': 'Bearer ' + access_token},
                json={'raw': base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')}, timeout=15)
            response.raise_for_status()
            provider_id = response.json()['id']
        except (requests.RequestException, ValueError, KeyError):
            with engine.begin() as conn:
                conn.execute(update(table).where(table.c.id == row['id']).values(state='needs_review'))
            logger.warning('Feedback email delivery requires review: %s', row['id'])
            continue
        with engine.begin() as conn:
            conn.execute(update(table).where(table.c.id == row['id']).values(state='sent', provider_id=provider_id))
        logger.warning('Feedback email accepted: %s', row['id'])

def deliver_pending(engine, table, logger):
    key, sender = os.getenv('RESEND_API_KEY'), os.getenv('NOTIFICATION_FROM')
    if not engine or not configured():
        return
    with _lock:
        with engine.connect() as conn:
            rows = list(conn.execute(select(table).where(table.c.state == 'pending', table.c.id.like('feedback-%')).limit(25)).mappings())
        if not rows:
            return
        if gmail_configured():
            return deliver_gmail(engine, table, rows, logger)
        for row in rows:
            started = row['first_attempt'] or int(time.time())
            # Resend idempotency keys expire after 24 hours. Hold uncertain sends
            # for review rather than risk duplicate email outside that window.
            if time.time() - started >= 23 * 3600:
                with engine.begin() as conn:
                    conn.execute(update(table).where(table.c.id == row['id']).values(state='needs_review'))
                logger.warning('Email notification requires delivery review: %s', row['id'])
                continue
            with engine.begin() as conn:
                conn.execute(update(table).where(table.c.id == row['id']).values(first_attempt=started))
            try:
                response = requests.post('https://api.resend.com/emails',
                    headers={'Authorization': 'Bearer ' + key, 'Idempotency-Key': row['id']},
                    json={'from': sender, 'to': [email for email in json.loads(row['recipients']) if email in RECIPIENTS],
                          'subject': row['subject'], 'text': row['body'], 'reply_to': row['reply_to']}, timeout=15)
                response.raise_for_status()
                provider_id = response.json()['id']
                with engine.begin() as conn:
                    conn.execute(update(table).where(table.c.id == row['id']).values(state='sent', provider_id=provider_id))
                logger.info('Email notification accepted: %s', row['id'])
            except (requests.RequestException, ValueError, KeyError):
                logger.warning('Email notification pending: %s', row['id'])

def run_worker(engine, table, logger):
    while True:
        try:
            deliver_pending(engine, table, logger)
        except Exception:
            logger.warning('Email retry deferred; notifications remain saved')
        time.sleep(60)
