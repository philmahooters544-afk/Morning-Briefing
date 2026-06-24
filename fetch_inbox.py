#!/usr/bin/env python3
"""
Build inbox-pack.md: cleaned plain text of every email in the Gmail `News`
label received in the last HOURS hours. Mirrors data-pack.md — committed to
the repo by the morning Action so the briefing reads it as the raw stream.

Auth : IMAP + a Gmail app password (no OAuth). Read-only; never alters mailbox.
Env  : GMAIL_USER, GMAIL_APP_PASSWORD   (GitHub Actions secrets)
Deps : standard library only.
"""

import os
import re
import html
import time
import email
import imaplib
import datetime as dt
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

# ---------------- config (edit freely) ----------------
LABEL = "News"
HOURS = 24                  # the window — change here
MAX_CHARS = 16000           # per-email body cap, keeps the pack sane
COARSE = "newer_than:2d"    # Gmail day-rounded net; precise cut done below
OUT = "inbox-pack.md"
# ------------------------------------------------------

USER = os.environ["GMAIL_USER"]
PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
NOW = dt.datetime.now(dt.timezone.utc)
CUTOFF = NOW - dt.timedelta(hours=HOURS)


def _dec(raw):
    """RFC2047-decode a header to str."""
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _html_to_text(h):
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table)>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    return html.unescape(h)


_NOISE = re.compile(
    r"^(view in browser|view online|unsubscribe|manage (your )?preferences|"
    r"update your preferences|add us to your address book|"
    r"you are receiving this|sent to |copyright|©|\[image\]).*$",
    re.I,
)


def _clean(text):
    text = html.unescape(text)
    text = text.replace("\u200c", "").replace("\xa0", " ").replace("\u00ad", "")
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"[ \t]+", " ", ln).strip()
        if not ln:
            lines.append("")
            continue
        if _NOISE.match(ln):
            continue
        lines.append(ln)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS].rstrip() + "\n\n… [truncated]"
    return out


def _body_text(msg):
    """Prefer text/plain; fall back to stripped text/html."""
    plain, html_part = "", ""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in str(part.get("Content-Disposition") or "").lower():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except (LookupError, TypeError):
            text = payload.decode("utf-8", errors="replace")
        if ctype == "text/plain":
            plain += text + "\n"
        else:
            html_part += text + "\n"

    if len(plain.strip()) >= 200:
        return _clean(plain)
    if html_part.strip():
        return _clean(_html_to_text(html_part))
    return _clean(plain)


def _find_all_mail(M):
    """Locate the All-Mail mailbox by RFC6154 \\All flag (locale-safe)."""
    typ, boxes = M.list()
    if typ == "OK":
        for b in boxes:
            line = b.decode(errors="replace")
            if "\\All" in line:
                m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
                if m:
                    return m.group(1)
    return "INBOX"


def _internaldate(meta):
    try:
        tt = imaplib.Internaldate2tuple(meta)
        if tt:
            return dt.datetime.fromtimestamp(time.mktime(tt), dt.timezone.utc)
    except Exception:
        pass
    return None


def fetch():
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(USER, PASSWORD)
    try:
        mailbox = _find_all_mail(M)
        M.select(f'"{mailbox}"', readonly=True)
        typ, data = M.uid("search", "X-GM-RAW", f'"label:{LABEL} {COARSE}"')
        uids = data[0].split() if (typ == "OK" and data and data[0]) else []
        items = []
        for uid in uids:
            typ, resp = M.uid("fetch", uid, "(INTERNALDATE BODY.PEEK[])")
            if typ != "OK" or not resp:
                continue
            meta = raw = None
            for part in resp:
                if isinstance(part, tuple):
                    meta, raw = part[0], part[1]
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            when = _internaldate(meta)
            if when is None:
                try:
                    when = parsedate_to_datetime(msg.get("Date"))
                    if when and when.tzinfo is None:
                        when = when.replace(tzinfo=dt.timezone.utc)
                except Exception:
                    when = None
            if when is not None and when < CUTOFF:
                continue
            items.append({
                "when": when or NOW,
                "from": _dec(msg.get("From")),
                "subject": _dec(msg.get("Subject")) or "(no subject)",
                "body": _body_text(msg),
            })
        return items
    finally:
        try:
            M.logout()
        except Exception:
            pass


def build():
    items = fetch()
    items.sort(key=lambda x: x["when"], reverse=True)
    p = [
        f"# Inbox Pack — {LABEL} — last {HOURS}h — as of "
        f"{NOW.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"_Raw newsletter stream for the briefing: {len(items)} message(s). "
        "Cleaned plain text; per-article source links are inline in each body._",
        "",
    ]
    for it in items:
        p.append("---")
        p.append(f"**From:** {it['from']}")
        p.append(f"**Subject:** {it['subject']}")
        p.append(f"**Received:** {it['when'].strftime('%Y-%m-%d %H:%M UTC')}")
        p.append("")
        p.append(it["body"] or "_[no extractable text]_")
        p.append("")
    if not items:
        p.append("_No messages in the window._")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(p))
    print(f"{OUT} written — {len(items)} message(s)")


if __name__ == "__main__":
    build()
