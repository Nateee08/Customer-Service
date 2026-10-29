# Hooked on Solar Customer Care

Customer service portal for the separate Nateee08/Customer-Service repository. No Housecall Pro integration. Does not modify the warehouse app.

## Render setup
Use the existing Customer Service web service when identified. Python runtime:
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app --workers 1 --worker-class gthread --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`
- Health path: `/healthz`

Prebuilt frontend assets are committed. Rebuild after frontend changes with `npm install && npm run build` and commit static/portal.js and static/portal.css.

## Persistent storage required
Set `DATABASE_URL` to a dedicated Postgres database, or set `DATA_DIR` to an attached persistent disk mount. Without either, public information pages work but account creation and ticket saving are disabled. Do not point this app at warehouse data. SQLite is suitable for a single service instance with a disk; use Postgres for multiple instances. Attachments are private records in the same database, capped at 20 MB per ticket.

## Smartsheet intake
User-selected sheet: https://app.smartsheet.com/sheets/XJcGxjwg5gVC5J6vfxQxmQW9QWjRC3jvx22gQjC1?view=grid
The share-link identifier is not treated as the numeric API sheet ID.

Configure runtime secrets, never commit values:
- SMARTSHEET_TOKEN
- SMARTSHEET_SHEET_ID (numeric API ID)
- SMARTSHEET_COLUMNS (JSON mapping `ticket`, `name`, `email`, `phone`, `address`, `category`, `subject`, `description`, `created`, `status` to actual column IDs)

Smartsheet columns are discovered by title unless an explicit mapping is supplied. Intake fields are sent server-side and attachments are copied to the ticket row. Refreshing tickets reads status from Smartsheet. Customer views never return other customers' rows. Queue positions and place-in-line messaging have been removed; legacy database and sheet columns are retained without being used.

Use the staff reconciliation action for pending deliveries. It checks for the unique ticket number before creating a row. Avoid concurrent reconciliation of the same ticket. Reconcile attachment transfers after uncertain failures before retrying to avoid duplicate attachments.

## Accounts and staff
Customers register with email and a password of at least 12 characters. Passwords use salted scrypt hashes; server-side sessions have random tokens, hashed in storage, and expire after seven days. HTTP-only, SameSite cookies, origin/CSRF checks, database-backed rate limits, server-side ownership checks and file signature validation are included. Production on Render uses Secure cookies.

Customer signup never grants staff access. After storage is configured, run `flask --app app create-admin` in the service shell to create a verified staff account or explicitly promote an existing verified staff email. This command prompts privately for the new password and revokes old sessions. Staff controls at `/staff` manage membership dates, 0/15/30 support minutes, work completion dates, pending intake delivery, and customer feedback.

For password reset, set SMTP_HOST, SMTP_PORT (587 default), SMTP_USER, SMTP_PASSWORD, SMTP_FROM and PUBLIC_URL. Until SMTP is configured, reset explains the setup requirement. No secret values belong in GitHub.

## Content and release checks
Original uploaded documents are encoded in documents.json and served at /documents/*.jpg. Branding uses the original logo from the subscription document. Website colors are white, black and orange. Warranty tracking begins on work completion; the original photographed warranty still says installation and should be replaced with an approved updated copy. Confirm the 60-day introductory offer dates before launch.

Included: customer login, private tickets and attachments, request status display, Basic/Gold information, customer QA feedback, staff profile editing, FAQs, learning guides, and official Tesla/SunStrong links. No billing or scheduling integration. Learning content is introductory; no live classes, AI chat, or video library is implied.

Before customer launch, connect and verify persistent storage, Smartsheet columns and permissions, email reset delivery, staff access, and status updates. Test two real customer accounts for isolation. Current automated tests cover signup, logout, CSRF, user-scoped tickets/files/reviews, staff access denial, documents and health. Run `python -m unittest test_app -v`.

## Staff email notifications
Customer feedback is queued transactionally in `care_email_outbox` for Alec and Customer Service. Password-reset messages remain customer-only. No historical submissions are emailed automatically. Service requests go to Smartsheet, not the email queue. Contact and complaint email links address Customer Service only.

Delivery requires `RESEND_API_KEY` and `NOTIFICATION_FROM` (a verified sending address) in the customer service Render environment. Until these are configured, submissions remain saved and emails remain pending. The worker sends over HTTPS every 60 seconds while the service is running; a sleeping free service cannot deliver until it wakes. Replies go to the submitting customer's email address. Attachments stay with the Smartsheet request and are not duplicated into notification emails.

Transient failures retry with the same provider idempotency key. Uncertain deliveries older than 23 hours are held as `needs_review` to avoid retries after the provider's idempotency window. A provider acceptance is not proof of inbox delivery; confirm using the provider's delivery records. Keep a single application worker unless a database-backed claim mechanism is added.

### Company Gmail sender (preferred)
Authorize **customerservice@hookedonsolar.com** using a company-owned Google Cloud OAuth client with Gmail API enabled and only `https://www.googleapis.com/auth/gmail.send` scope. Request offline access and store `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` securely in the customer service Render service, never in GitHub. Use an internal Workspace OAuth app where available; a testing-mode external app is unsuitable for permanent unattended sending. This route takes precedence over Resend when all three settings exist.

The Google worker sends feedback only to Alec and Customer Service. It does not read inboxes or forward password reset links. Gmail has no send idempotency key: ambiguous send failures are held for review (`needs_review`), and a process interrupted during a send leaves `sending` for review. Do not reset these states without checking the mailbox's Sent folder. No live email delivery has been verified until the mailbox authorization is installed and an end-to-end test succeeds.
