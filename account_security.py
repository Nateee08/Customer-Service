"""Email verification before issuing any customer session."""
import secrets, time, re
from flask import request, redirect, abort, make_response, render_template
from sqlalchemy import Table, Column, String, Integer, Text, select, insert, update, delete
from werkzeug.security import generate_password_hash, check_password_hash
import notifications


def define_table(meta):
    return Table('care_login_challenges', meta,
        Column('token', String, primary_key=True), Column('user_id', String, nullable=False),
        Column('code_hash', Text, nullable=False), Column('expires', Integer, nullable=False),
        Column('attempts', Integer, nullable=False, default=0))


def install(a):
    app, engine, challenges = a.app, a.engine, a.challenges

    def start(uid, email):
        a.rate('verification-send:' + uid, 3)
        token = secrets.token_urlsafe(32)
        code = ''.join(secrets.choice('0123456789') for _ in range(8))
        try:
            notifications.send_account_email(email, 'Your Hooked on Solar verification code',
                'Your sign-in code is: ' + code + '\n\nThis code expires in 10 minutes and can be used once. '
                'Enter it only on the Hooked on Solar customer website. Never share this code. '
                'If you did not request it, you can ignore this message.')
        except Exception:
            app.logger.warning('Account verification email unavailable')
            return a.auth_view('login', 'We could not send a verification code. Please try again shortly or contact customer service. Your account has not been signed in.', 503)
        with engine.begin() as c:
            c.execute(delete(challenges).where(challenges.c.user_id == uid))
            c.execute(delete(challenges).where(challenges.c.expires < int(time.time())))
            c.execute(insert(challenges).values(token=a.digest(token), user_id=uid,
                code_hash=generate_password_hash(code), expires=int(time.time())+600, attempts=0))
        r = redirect('/verify-account')
        r.set_cookie('care_challenge', token, max_age=600, httponly=True,
            secure=a.secure_cookies(), samesite='Lax')
        r.delete_cookie('care_session')
        return r

    a.start_verification = start

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
                    session = secrets.token_urlsafe(32)
                    c.execute(insert(a.sessions).values(token=a.digest(session), user_id=row['user_id'], expires=int(time.time())+43200))
                    r = redirect('/')
                    r.set_cookie('care_session', session, httponly=True, secure=a.secure_cookies(), samesite='Lax', max_age=43200)
                    r.delete_cookie('care_challenge')
                    return r
                message = 'That code is not valid. Please try again.'
        csrf = request.cookies.get('care_csrf') or secrets.token_urlsafe(32)
        r = make_response(render_template('verify.html', csrf=csrf, message=message), 400 if message else 200)
        r.set_cookie('care_csrf', csrf, httponly=True, secure=a.secure_cookies(), samesite='Lax')
        return r
