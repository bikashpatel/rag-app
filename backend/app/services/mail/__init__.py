"""The transactional mailer (docs/briefs/signup-auth.md 6): every email to a user goes through the email_outbox table,
which the worker drains over SMTP (MAIL_*). Alerts keep their own channels (app.ops.channels)."""
