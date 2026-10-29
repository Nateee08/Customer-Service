"""Durable staff notifications; requires a verified Resend sender before delivery."""
import json, os, threading, time
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

def deliver_pending(engine, table, logger):
    key, sender = os.getenv('RESEND_API_KEY'), os.getenv('NOTIFICATION_FROM')
    if not engine or not key or not sender:
        return
    with _lock:
        with engine.connect() as conn:
            rows = list(conn.execute(select(table).where(table.c.state == 'pending').limit(25)).mappings())
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
