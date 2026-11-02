"""Email verification before issuing any customer session."""
import secrets, time, re, os, threading
from flask import request, redirect, abort, make_response, render_template, jsonify
from sqlalchemy import Table, Column, String, Integer, Text, select, insert, update, delete
from werkzeug.security import generate_password_hash, check_password_hash
import notifications

_delivery_slots = threading.BoundedSemaphore(4)


def define_delivery_table(meta):
    return Table('care_account_deliveries', meta,
        Column('token', String, primary_key=True),
        Column('state', String, nullable=False),
        Column('started', Integer, nullable=False))


def define_table(meta):
    return Table('care_login_challenges', meta,
        Column('token', String, primary_key=True), Column('user_id', String, nullable=False),
        Column('code_hash', Text, nullable=False), Column('expires', Integer, nullable=False),
        Column('attempts', Integer, nullable=False, default=0))


def define_security_tables(meta):
    devices=Table('care_trusted_devices',meta,
        Column('token',String,primary_key=True),Column('user_id',String,nullable=False),
        Column('agent',String,nullable=False),Column('label',String,nullable=False),
        Column('expires',Integer,nullable=False),Column('used',Integer,nullable=False))
    prefs=Table('care_security_preferences',meta,
        Column('user_id',String,primary_key=True),Column('every_login',Integer,nullable=False,default=0))
    return devices,prefs


def install(a):
    app, engine, challenges = a.app, a.engine, a.challenges
    deliveries = a.account_deliveries
    app.config.setdefault('ACCOUNT_EMAIL_ASYNC', bool(os.getenv('RENDER')))
    devices,prefs=a.trusted_devices,a.security_preferences

    def device_label():
        ua=request.headers.get('User-Agent','')
        browser='Edge' if 'Edg/' in ua else 'Chrome' if 'Chrome/' in ua else 'Firefox' if 'Firefox/' in ua else 'Safari' if 'Safari/' in ua else 'Browser'
        platform='iPhone' if 'iPhone' in ua else 'iPad' if 'iPad' in ua else 'Android' if 'Android' in ua else 'Windows' if 'Windows' in ua else 'Mac' if 'Macintosh' in ua else 'device'
        return browser+' on '+platform

    def session_response(c,uid):
        session=secrets.token_urlsafe(32)
        c.execute(insert(a.sessions).values(token=a.digest(session),user_id=uid,expires=int(time.time())+43200))
        r=redirect('/')
        r.set_cookie('care_session',session,httponly=True,secure=a.secure_cookies(),samesite='Lax',max_age=43200)
        r.delete_cookie('care_challenge')
        return r

    def remember(c,r,uid,expires=None):
        expires=expires or int(time.time())+30*86400
        old=a.digest(request.cookies.get('care_trusted_device',''))
        c.execute(delete(devices).where(devices.c.token==old,devices.c.user_id==uid))
        c.execute(delete(devices).where(devices.c.expires<int(time.time())))
        token=secrets.token_urlsafe(32)
        c.execute(insert(devices).values(token=a.digest(token),user_id=uid,
            agent=a.digest(request.headers.get('User-Agent','')),label=device_label(),expires=expires,used=int(time.time())))
        r.set_cookie('care_trusted_device',token,max_age=max(1,expires-int(time.time())),httponly=True,secure=a.secure_cookies(),samesite='Lax')

    def trusted_login(uid):
        token=request.cookies.get('care_trusted_device')
        if not token:return None
        with engine.begin() as c:
            every=c.execute(select(prefs.c.every_login).where(prefs.c.user_id==uid)).scalar()
            if every:return None
            device=c.execute(select(devices).where(devices.c.token==a.digest(token),devices.c.user_id==uid).with_for_update()).mappings().first()
            if not device or device['expires']<=time.time() or device['agent']!=a.digest(request.headers.get('User-Agent','')):return None
            r=session_response(c,uid)
            remember(c,r,uid,device['expires'])
            return r
    a.trusted_login=trusted_login

    @app.route('/account/settings',methods=['GET','POST'])
    def settings():
        u=a.required();message=''
        with engine.begin() as c:
            if request.method=='POST':
                a.rate('security-settings:'+u['id'],10)
                if not check_password_hash(u['password'],request.form.get('password','')):
                    message='Your current password is incorrect. No settings were changed.'
                else:
                    action=request.form.get('action')
                    if action=='save':
                        every=int(request.form.get('every_login')=='on')
                        existing=c.execute(select(prefs.c.user_id).where(prefs.c.user_id==u['id'])).scalar()
                        if existing:c.execute(update(prefs).where(prefs.c.user_id==u['id']).values(every_login=every))
                        else:c.execute(insert(prefs).values(user_id=u['id'],every_login=every))
                        if every:c.execute(delete(devices).where(devices.c.user_id==u['id']))
                        message='Security preferences saved.'
                    elif action=='remove':
                        c.execute(delete(devices).where(devices.c.user_id==u['id'],devices.c.token==request.form.get('device')))
                        message='Device removed. Its next sign-in will require an email code.'
                    elif action=='revoke-all':
                        c.execute(delete(devices).where(devices.c.user_id==u['id']))
                        c.execute(delete(a.sessions).where(a.sessions.c.user_id==u['id'],a.sessions.c.token!=a.digest(request.cookies.get('care_session',''))))
                        message='Other sessions signed out and all remembered devices removed.'
            every=c.execute(select(prefs.c.every_login).where(prefs.c.user_id==u['id'])).scalar() or 0
            remembered=[dict(row) for row in c.execute(select(devices).where(devices.c.user_id==u['id'],devices.c.expires>int(time.time())).order_by(devices.c.used.desc())).mappings()]
        from datetime import datetime
        from zoneinfo import ZoneInfo
        for device in remembered:
            device['expiry']=datetime.fromtimestamp(device['expires'],ZoneInfo('America/Los_Angeles')).strftime('%b %d, %Y')
            device['current']=device['token']==a.digest(request.cookies.get('care_trusted_device',''))
        csrf=request.cookies.get('care_csrf') or secrets.token_urlsafe(32)
        r=make_response(render_template('security.html',csrf=csrf,email=u['email'],every=every,devices=remembered,message=message))
        r.headers['Cache-Control']='no-store'
        r.set_cookie('care_csrf',csrf,httponly=True,secure=a.secure_cookies(),samesite='Lax')
        return r


    def delivery_state(conn, token):
        row = conn.execute(select(deliveries).where(deliveries.c.token == token)).mappings().first()
        # Challenges created before this release were saved only after delivery.
        if not row:
            return 'sent'
        if row['state'] == 'sending' and time.time() - row['started'] > 45:
            return 'failed'
        return row['state']

    def deliver(token, email, code):
        started = time.monotonic()
        state = 'failed'
        try:
            notifications.send_account_email(email, 'Your Hooked on Solar verification code',
                'Your sign-in code is: ' + code + '\n\nThis code expires in 10 minutes and can be used once. '
                'Enter it only on the Hooked on Solar customer website. Never share this code. '
                'If you did not request it, you can ignore this message.')
            state = 'sent'
            app.logger.warning('Account verification email accepted (%.1fs)', time.monotonic() - started)
        except Exception as exc:
            app.logger.warning('Account verification email failed: %s (%.1fs)',
                notifications.account_email_error(exc), time.monotonic() - started)
        finally:
            try:
                with engine.begin() as c:
                    c.execute(update(deliveries).where(deliveries.c.token == token).values(state=state))
            except Exception as exc:
                app.logger.error('Account delivery status could not be saved: %s', type(exc).__name__)
            _delivery_slots.release()

    def start(uid, email):
        a.rate('verification-send:' + uid, 3)
        token = secrets.token_urlsafe(32)
        code = ''.join(secrets.choice('0123456789') for _ in range(8))
        token_hash = a.digest(token)
        code_hash = generate_password_hash(code)
        if not _delivery_slots.acquire(blocking=False):
            return a.auth_view('login', 'Verification is busy. Please try signing in again shortly.', 503)
        try:
            with engine.begin() as c:
                c.execute(delete(deliveries).where(deliveries.c.token.in_(select(challenges.c.token).where(challenges.c.user_id == uid))))
                c.execute(delete(deliveries).where(deliveries.c.started < int(time.time()) - 600))
                c.execute(delete(challenges).where(challenges.c.user_id == uid))
                c.execute(delete(challenges).where(challenges.c.expires < int(time.time())))
                c.execute(insert(challenges).values(token=token_hash, user_id=uid,
                    code_hash=code_hash, expires=int(time.time())+600, attempts=0))
                c.execute(insert(deliveries).values(token=token_hash, state='sending', started=int(time.time())))
            if app.config['ACCOUNT_EMAIL_ASYNC']:
                threading.Thread(target=deliver, args=(token_hash, email, code),
                    name='account-email', daemon=True).start()
            else:
                deliver(token_hash, email, code)
        except Exception:
            _delivery_slots.release()
            raise
        r = redirect('/verify-account')
        r.set_cookie('care_challenge', token, max_age=600, httponly=True,
            secure=a.secure_cookies(), samesite='Lax')
        r.delete_cookie('care_session')
        return r

    a.start_verification = start

    @app.get('/api/account-verification')
    def status():
        a.db_required()
        token = a.digest(request.cookies.get('care_challenge', ''))
        with engine.connect() as c:
            row = c.execute(select(challenges).where(challenges.c.token == token)).mappings().first()
            if not row or row['expires'] <= time.time() or row['attempts'] >= 5:
                return jsonify(state='expired')
            return jsonify(state=delivery_state(c, token))

    @app.post('/verify-account/resend')
    def resend():
        a.db_required()
        token = a.digest(request.cookies.get('care_challenge', ''))
        with engine.connect() as c:
            row = c.execute(select(challenges.c.user_id, a.users.c.email).join(
                a.users, challenges.c.user_id == a.users.c.id).where(
                challenges.c.token == token, challenges.c.expires > int(time.time()))).mappings().first()
            delivery = c.execute(select(deliveries).where(deliveries.c.token == token)).mappings().first()
        if not row:
            return a.auth_view('login', 'Please sign in again to request a new code.', 400)
        if delivery and time.time() - delivery['started'] < 30:
            return redirect('/verify-account')
        return start(row['user_id'], row['email'])

    @app.route('/verify-account', methods=['GET', 'POST'])
    def verify():
        a.db_required()
        token = a.digest(request.cookies.get('care_challenge', ''))
        message = ''
        with engine.begin() as c:
            row = c.execute(select(challenges).where(challenges.c.token == token).with_for_update()).mappings().first()
            if not row or row['expires'] <= time.time() or row['attempts'] >= 5:
                return a.auth_view('login', 'Your verification code has expired or reached its attempt limit. Sign in again for a new code.', 400)
            if request.method == 'POST':
                code = request.form.get('code', '').strip()
                c.execute(update(challenges).where(challenges.c.token == token).values(attempts=challenges.c.attempts+1))
                if re.fullmatch(r'\d{8}', code) and check_password_hash(row['code_hash'], code):
                    # Row lock and deletion make this challenge single use, even on concurrent requests.
                    c.execute(delete(challenges).where(challenges.c.token == token))
                    c.execute(delete(deliveries).where(deliveries.c.token == token))
                    r=session_response(c,row['user_id'])
                    every=c.execute(select(prefs.c.every_login).where(prefs.c.user_id==row['user_id'])).scalar()
                    if request.form.get('remember_device')=='on' and not every:remember(c,r,row['user_id'])
                    return r
                message = 'That code is not valid. Please try again.'
            state = delivery_state(c, token)
        csrf = request.cookies.get('care_csrf') or secrets.token_urlsafe(32)
        r = make_response(render_template('verify.html', csrf=csrf, message=message, state=state), 400 if message else 200)
        r.set_cookie('care_csrf', csrf, httponly=True, secure=a.secure_cookies(), samesite='Lax')
        return r
