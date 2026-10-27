"""Email sender (disabled by default).

``send_report`` sends one report file from 06_REPORTS by SMTP with STARTTLS.
Nothing is sent unless the caller passes ``send_enabled=True``. While disabled
the function returns DISABLED straight away: it reads no credentials, imports
no ``smtplib`` and opens no connection.

Credentials come only from a ``.env`` file at the project root (or ``env_path``):
AIC_SMTP_USER, AIC_SMTP_PASSWORD, AIC_SMTP_HOST (default
smtp.gmail.com), AIC_SMTP_PORT (default 587). This module never creates a
.env, never writes, logs, prints or returns the password, and keeps it out of
error text.

Every call appends one line to ``07_LOGS/email_log.csv``: time, status,
recipient count, attachment file name. No addresses, no credentials.

Statuses: DISABLED, NO_RECIPIENTS, NO_CREDENTIALS, DRY_RUN, SENT, FAILED.
``send_report`` returns a dict and prints nothing.
"""
from __future__ import annotations

import datetime
from email.message import EmailMessage
from pathlib import Path

import src.daily as daily

LOG_NAME = "email_log.csv"
DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587
DEFAULT_SUBJECT = "Client attendance report"
DEFAULT_BODY = "Attached is the Client attendance report. HR decides every flag."

# Tests replace this with a fake class. None means "import smtplib when needed".
SMTP_CLASS = None


def read_env(path) -> dict:
    """Tiny KEY=VALUE reader. Skips blanks and # comments, strips matching quotes."""
    values: dict = {}
    p = Path(path)
    if not p.is_file():
        return values
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        if key:
            values[key] = val
    return values


def _log(status: str, count: int, attachment: str) -> None:
    try:
        logs_dir = Path(daily.LOGS_DIR)
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = logs_dir / LOG_NAME
        is_new = not path.exists()
        name = str(attachment).replace('"', "'").replace("\n", " ").replace("\r", " ")
        with open(path, "a", encoding="utf-8", newline="") as f:
            if is_new:
                f.write("run_time,status,recipients_count,attachment\n")
            f.write(f'{datetime.datetime.now().isoformat(timespec="seconds")},'
                    f'{status},{count},"{name}"\n')
    except OSError:
        pass  # logging must never break or leak


def _result(status: str, count: int, attachment: str, error=None) -> dict:
    _log(status, count, attachment)
    return {"status": status, "recipients_count": count,
            "attachment": attachment, "error": error}


def _clean_recipients(recipients) -> list[str]:
    if not recipients:
        return []
    if isinstance(recipients, str):
        recipients = [recipients]
    return [str(r).strip() for r in recipients if r and str(r).strip()]


def _under_reports(report_path) -> Path | None:
    try:
        p = Path(report_path).resolve()
        p.relative_to(Path(daily.REPORTS_DIR).resolve())
    except (ValueError, OSError, TypeError):
        return None
    return p if p.is_file() else None


def _build_message(sender: str, recipients: list[str], subject, body, report: Path) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject or DEFAULT_SUBJECT
    msg.set_content(body or DEFAULT_BODY)
    suffix = report.suffix.lower()
    types = {".xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
             ".html": ("text", "html"), ".txt": ("text", "plain"),
             ".json": ("application", "json")}
    maintype, subtype = types.get(suffix, ("application", "octet-stream"))
    msg.add_attachment(report.read_bytes(), maintype=maintype, subtype=subtype,
                       filename=report.name)
    return msg


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def send_report(report_path, recipients=None, subject=None, body=None,
                send_enabled=False, env_path=None, dry_run=False) -> dict:
    name = Path(str(report_path)).name if report_path else ""
    recips = _clean_recipients(recipients)
    count = len(recips)

    if not dry_run and not send_enabled:
        return _result("DISABLED", count, name)

    if not recips:
        return _result("NO_RECIPIENTS", 0, name)
    if any(("\n" in r or "\r" in r) for r in recips):
        return _result("FAILED", count, name, "invalid recipient")

    report = _under_reports(report_path)
    if report is None:
        return _result("FAILED", count, name,
                       "attachment must be an existing file under 06_REPORTS")

    if dry_run:
        try:
            _build_message("dry-run@example.invalid", recips, subject, body, report)
        except Exception as exc:  # noqa: BLE001
            return _result("FAILED", count, name, type(exc).__name__)
        return _result("DRY_RUN", count, name)

    env = read_env(env_path if env_path is not None else Path(daily.PROJECT_ROOT) / ".env")
    user = env.get("AIC_SMTP_USER", "")
    password = env.get("AIC_SMTP_PASSWORD", "")
    if not user or not password:
        return _result("NO_CREDENTIALS", count, name)
    host = env.get("AIC_SMTP_HOST") or DEFAULT_HOST
    try:
        port = int(env.get("AIC_SMTP_PORT") or DEFAULT_PORT)
    except ValueError:
        return _result("FAILED", count, name, "invalid AIC_SMTP_PORT")

    try:
        msg = _build_message(user, recips, subject, body, report)
        smtp_cls = SMTP_CLASS
        if smtp_cls is None:
            import smtplib  # imported only when a real send is attempted
            smtp_cls = smtplib.SMTP
        with smtp_cls(host, port, timeout=30) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        return _result("FAILED", count, name, _scrub(type(exc).__name__, password))
    return _result("SENT", count, name)


_SUCCESS = {"DISABLED", "SENT", "DRY_RUN"}


def main() -> int:
    """Entry point for Task Scheduler. Returns 0 on success, 1 on failure."""
    result = send_report(None)
    return 0 if result["status"] in _SUCCESS else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
