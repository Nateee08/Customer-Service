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
- SMARTSHEET_COLUMNS (JSON mapping `ticket`, `name`, `email`, `phone`, `address`, `category`, `subject`, `description`, `created`, `status`, `queue` to actual column IDs)

Ticket, email, name, subject, description, status and queue mappings are required before connection is considered configured. Intake fields are sent server-side and attachments are copied to the ticket row. Refreshing tickets reads status and queue from Smartsheet. Staff assign the actual intake queue order considering urgency and membership priority. Customer views never return other customers' rows. No invented queue number is shown while disconnected.

Use the staff reconciliation action for pending deliveries. It checks for the unique ticket number before creating a row. Avoid concurrent reconciliation of the same ticket. Reconcile attachment transfers after uncertain failures before retrying to avoid duplicate attachments.

## Accounts and staff
Customers register with email and a password of at least 12 characters. Passwords use salted scrypt hashes; server-side sessions have random tokens, hashed in storage, and expire after seven days. HTTP-only, SameSite cookies, origin/CSRF checks, database-backed rate limits, server-side ownership checks and file signature validation are included. Production on Render uses Secure cookies.

Customer signup never grants staff access. After storage is configured, run `flask --app app create-admin` in the service shell to create a verified staff account or explicitly promote an existing verified staff email. This command prompts privately for the new password and revokes old sessions. Staff controls at `/staff` manage membership dates, 0/15/30 support minutes, work completion dates, pending intake delivery, and customer feedback.

For password reset, set SMTP_HOST, SMTP_PORT (587 default), SMTP_USER, SMTP_PASSWORD, SMTP_FROM and PUBLIC_URL. Until SMTP is configured, reset explains the setup requirement. No secret values belong in GitHub.

## Content and release checks
Original uploaded documents are encoded in documents.json and served at /documents/*.jpg. Branding uses the original logo from the subscription document. Website colors are white, black and orange. Warranty tracking begins on work completion; the original photographed warranty still says installation and should be replaced with an approved updated copy. Confirm the 60-day introductory offer dates before launch.

Included: customer login, private tickets and attachments, queue/status display, Basic/Gold information, customer QA feedback, staff profile editing, FAQs, learning guides, and official Tesla/SunStrong links. No billing or scheduling integration. Learning content is introductory; no live classes, AI chat, or video library is implied.

Before customer launch, connect and verify persistent storage, Smartsheet columns and permissions, email reset delivery, staff access, and actual queue changes. Test two real customer accounts for isolation. Current automated tests cover signup, logout, CSRF, user-scoped tickets/files/reviews, staff access denial, documents and health. Run `python -m unittest test_app -v`.

## Staff email notifications
New service requests and customer feedback are queued transactionally in `care_email_outbox` for Roger, Alec, and Customer Service. Password-reset messages remain customer-only. No historical submissions are emailed automatically.

Delivery requires `RESEND_API_KEY` and `NOTIFICATION_FROM` (a verified sending address) in the customer service Render environment. Until these are configured, submissions remain saved and emails remain pending. The worker sends over HTTPS every 60 seconds while the service is running; a sleeping free service cannot deliver until it wakes. Replies go to the submitting customer's email address. Attachments stay with the Smartsheet request and are not duplicated into notification emails.

Transient failures retry with the same provider idempotency key. Uncertain deliveries older than 23 hours are held as `needs_review` to avoid retries after the provider's idempotency window. A provider acceptance is not proof of inbox delivery; confirm using the provider's delivery records. Keep a single application worker unless a database-backed claim mechanism is added.
