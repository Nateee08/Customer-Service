"""Stable customer references, separate from each request's private primary key."""
import datetime
import re
from zoneinfo import ZoneInfo

from sqlalchemy import Table, Column, String, Integer, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


def define_tables(meta):
    customers = Table('care_customer_numbers', meta,
        Column('user_id', String, primary_key=True),
        Column('base_number', String, nullable=False, unique=True))
    requests = Table('care_ticket_numbers', meta,
        Column('ticket_id', String, primary_key=True),
        Column('reference', String, nullable=False),
        Column('synced', Integer, nullable=False, default=0))
    counters = Table('care_number_counters', meta,
        Column('name', String, primary_key=True),
        Column('value', Integer, nullable=False))
    return customers, requests, counters


def subscribed(user, created):
    if not user or user.get('plan') not in ('Basic', 'Gold'):
        return False
    day = datetime.datetime.fromisoformat(created.replace('Z', '+00:00')).astimezone(
        ZoneInfo('America/Los_Angeles')).date().isoformat()
    return ((not user.get('membership_start') or user['membership_start'] <= day)
        and (not user.get('membership_end') or day <= user['membership_end']))


def job_reference(jobs):
    # Natural order, deduplication, and no accidental float suffix from numeric cells.
    cleaned = {re.sub(r'^(\d+)\.0$', r'\1', str(j).strip()) for j in jobs if str(j).strip()}
    def key(value):
        return [(0, int(p)) if p.isdigit() else (1, p.casefold()) for p in re.split(r'(\d+)', value)]
    return 'HOS-' + '/'.join(sorted(cleaned, key=key)) if cleaned else None


def assign(connection, tables, ticket, user, resolution=None):
    """Call inside a transaction. Unknown client lookup leaves assignment pending.

    The tiny counter-row write serializes allocation across workers, including two
    first requests for the same customer. No network work happens under this lock.
    """
    customers, requests, counters = tables
    existing = connection.execute(select(requests.c.reference).where(
        requests.c.ticket_id == ticket['id'])).scalar()
    if existing:
        return existing
    if resolution is None:
        return None
    upsert = pg_insert if connection.dialect.name == 'postgresql' else sqlite_insert
    connection.execute(upsert(counters).values(name='soc', value=0).on_conflict_do_nothing())
    connection.execute(update(counters).where(counters.c.name == 'soc').values(value=counters.c.value))
    # Re-read after obtaining the lock; another request may just have committed.
    existing = connection.execute(select(requests.c.reference).where(
        requests.c.ticket_id == ticket['id'])).scalar()
    if existing:
        return existing
    base = job_reference(resolution['job_numbers'])
    if not base:
        if resolution['record']:
            return None  # A known HOS customer needs an actual database job number.
        # An account's SOC allocation survives later HOS matches and plan changes.
        # Always resolve HOS jobs first: one account may submit for multiple systems.
        base = connection.execute(select(customers.c.base_number).where(
            customers.c.user_id == ticket['user_id'])).scalar()
        if not base:
            number = connection.execute(update(counters).where(counters.c.name == 'soc').values(
                value=counters.c.value + 1).returning(counters.c.value)).scalar_one()
            base = f'SOC-{number:05d}'
            connection.execute(customers.insert().values(user_id=ticket['user_id'], base_number=base))
    reference = ('HPS-' if subscribed(user, ticket['created']) else '') + base
    connection.execute(requests.insert().values(ticket_id=ticket['id'], reference=reference, synced=0))
    return reference


def matches_request(row, columns, ticket_id):
    """A repeat customer's display reference is deliberately NOT a dedupe key."""
    values = {str(c['columnId']): c.get('value') for c in row.get('cells', [])}
    if values.get(str(columns.get('ticket'))) == ticket_id:
        return True  # Legacy rows before customer-based numbering.
    notes = str(values.get(str(columns.get('description')), ''))
    return any(line.strip() in ('Website request ID: ' + ticket_id, 'Website ticket: ' + ticket_id)
               for line in notes.splitlines())
