#!/usr/bin/env python3
"""
Build the daily market data-pack for the morning briefing.

Reliable, free core : market tables (yfinance) + credit OAS (FRED).
Best-effort         : earnings + macro calendars (Finnhub's free tier may gate
                      these; if so the pack says so and Claude fills via the
                      newsletter stream + web search).

Output: data-pack.md  (Markdown, so Claude reads it directly)
"""

import os
import datetime as dt
import requests
import yfinance as yf

TODAY = dt.date.today()
NOW_UTC = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

FRED_KEY = os.environ.get("FRED_KEY", "")
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")

# ---------------- config (edit freely) ----------------
EQUITY = {
    "^GDAXI": "DAX", "^MDAXI": "MDAX", "^STOXX50E": "Euro Stoxx 50",
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^N225": "Nikkei 225",
    "^HSI": "Hang Seng", "000300.SS": "CSI 300",
}
# NOTE on rates: Yahoo's ^TNX/^FVX/^TYX are quoted as the yield in percent
# (e.g. 4.25 = 4.25%). Verify on first run; if you see ~42.5, divide by 10.
RATES = {"^TNX": "US 10Y", "^FVX": "US 5Y", "^TYX": "US 30Y"}
FX = {
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY",
    "EURCHF=X": "EUR/CHF", "EURGBP=X": "EUR/GBP",
}
COMMODITIES = {
    "GC=F": "Gold", "SI=F": "Silver", "CL=F": "WTI", "BZ=F": "Brent",
    "NG=F": "Henry Hub",
}
FRED_OAS = {
    "BAMLC0A0CM": "US IG OAS",
    "BAMLH0A0HYM2": "US HY OAS",
    "BAMLHE00EHYIOAS": "Euro HY OAS",
}
# ------------------------------------------------------


def _pct(a, b):
    try:
        return (a / b - 1) * 100
    except Exception:
        return None


def _fmt_pct(x):
    return f"{x:+.2f}%" if x is not None else ""


def _close_frame(tickers, period):
    data = yf.download(list(tickers), period=period, interval="1d",
                       auto_adjust=True, progress=False)
    return data["Close"] if "Close" in data.columns.get_level_values(0) else data


def returns_table(tickers):
    """rows: [name, level, 1D, 1W, 1M, YTD] in % returns."""
    rows = []
    if not tickers:
        return rows
    close = _close_frame(tickers, "1y")
    for tk, name in tickers.items():
        try:
            s = close[tk].dropna()
            if s.empty:
                rows.append([name, "n/a", "", "", "", ""]); continue
            last = s.iloc[-1]
            d1 = _pct(last, s.iloc[-2]) if len(s) > 1 else None
            w1 = _pct(last, s.iloc[-6]) if len(s) > 6 else None
            m1 = _pct(last, s.iloc[-22]) if len(s) > 22 else None
            ytd_base = s[s.index.year == TODAY.year]
            ytd = _pct(last, ytd_base.iloc[0]) if not ytd_base.empty else None
            rows.append([name, f"{last:,.2f}", _fmt_pct(d1), _fmt_pct(w1),
                         _fmt_pct(m1), _fmt_pct(ytd)])
        except Exception:
            rows.append([name, "error", "", "", "", ""])
    return rows


def rates_table(tickers):
    """rows: [name, level%, 1D, 1W, 1M] with changes in bps."""
    rows = []
    if not tickers:
        return rows
    close = _close_frame(tickers, "3mo")
    for tk, name in tickers.items():
        try:
            s = close[tk].dropna()
            if s.empty:
                rows.append([name, "n/a", "", "", ""]); continue
            last = s.iloc[-1]
            bps = lambda i: (f"{(last - s.iloc[i]) * 100:+.0f} bps"
                             if len(s) > abs(i) else "")
            rows.append([name, f"{last:.2f}%", bps(-2), bps(-6), bps(-22)])
        except Exception:
            rows.append([name, "error", "", "", ""])
    return rows


def fred_obs(series_id, n=30):
    if not FRED_KEY:
        return []
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series_id, "api_key": FRED_KEY,
                "file_type": "json", "sort_order": "desc", "limit": n},
        timeout=30,
    )
    obs = [o for o in r.json().get("observations", []) if o["value"] not in (".", "")]
    return [(o["date"], float(o["value"])) for o in obs]  # newest first


def credit_table():
    """rows: [name, OAS bps, 1D, 1W, 1M] (OAS from FRED, in percent -> bps)."""
    rows = []
    if not FRED_KEY:
        return rows
    for sid, name in FRED_OAS.items():
        try:
            obs = fred_obs(sid)
            if not obs:
                rows.append([name, "n/a", "", "", ""]); continue
            last = obs[0][1]
            chg = lambda i: (f"{(last - obs[i][1]) * 100:+.0f} bps"
                             if len(obs) > i else "")
            rows.append([name, f"{last * 100:.0f} bps", chg(1), chg(5), chg(21)])
        except Exception:
            rows.append([name, "error", "", "", ""])
    return rows


def finnhub_get(path, params):
    if not FINNHUB_KEY:
        return None
    try:
        params = dict(params, token=FINNHUB_KEY)
        r = requests.get(f"https://finnhub.io/api/v1/{path}", params=params, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def earnings_rows():
    frm = (TODAY - dt.timedelta(days=1)).isoformat()
    data = finnhub_get("calendar/earnings", {"from": frm, "to": TODAY.isoformat()})
    if not data or not data.get("earningsCalendar"):
        return None
    rows = []
    for e in data["earningsCalendar"]:
        rows.append([
            f"{e.get('date','')} {e.get('hour','')}".strip(),
            e.get("symbol", ""),
            e.get("revenueActual", ""), e.get("revenueEstimate", ""),
            e.get("epsActual", ""), e.get("epsEstimate", ""),
        ])
    return rows


def macro_rows():
    data = finnhub_get("calendar/economic", {})
    if not data or not data.get("economicCalendar"):
        return None
    rows = []
    for ev in data["economicCalendar"]:
        rows.append([
            ev.get("time", ""), ev.get("country", ""), ev.get("event", ""),
            ev.get("actual", ""), ev.get("estimate", ""), ev.get("prev", ""),
        ])
    return rows


def md_table(headers, rows):
    if not rows:
        return "_unavailable — Claude to fill from the stream / web search_\n"
    out = "| " + " | ".join(headers) + " |\n"
    out += "| " + " | ".join("---" for _ in headers) + " |\n"
    for r in rows:
        out += "| " + " | ".join("" if c is None else str(c) for c in r) + " |\n"
    return out


def build():
    p = [f"# Data Pack — as of {NOW_UTC}\n",
         "_Numbers below are ground truth for the briefing. "
         "Euro-area sovereign yields (Bund/BTP/Gilt) and CDS indices "
         "(iTraxx/CDX) are NOT here — take from the stream and tag `[indicative]`._\n"]

    p.append("\n## Equity (returns)\n")
    p.append(md_table(["Index", "Level", "1D", "1W", "1M", "YTD"], returns_table(EQUITY)))

    p.append("\n## Rates — US Treasuries (level + bps)\n")
    p.append(md_table(["Bond", "YTM", "1D", "1W", "1M"], rates_table(RATES)))
    p.append("_Bund / BTP / Gilt 10Y: `[indicative]` from stream._\n")

    p.append("\n## Credit — ICE BofA OAS (FRED)\n")
    p.append(md_table(["Index", "OAS", "1D", "1W", "1M"], credit_table()))
    p.append("_CDS indices (iTraxx Main/Xover, CDX IG/HY): no free feed — "
             "`[indicative]` or omit._\n")

    p.append("\n## FX (returns)\n")
    p.append(md_table(["Pair", "Level", "1D", "1W", "1M", "YTD"], returns_table(FX)))

    p.append("\n## Commodities (returns)\n")
    p.append(md_table(["Asset", "Level", "1D", "1W", "1M", "YTD"], returns_table(COMMODITIES)))

    p.append("\n## Earnings calendar (yesterday → today)\n")
    p.append(md_table(["Date/Time", "Company", "Rev Actual", "Rev Exp",
                       "EPS Actual", "EPS Exp"], earnings_rows() or []))

    p.append("\n## Macro calendar\n")
    p.append(md_table(["Time", "Country", "Item", "Actual", "Consensus",
                       "Previous"], macro_rows() or []))

    with open("data-pack.md", "w", encoding="utf-8") as f:
        f.write("\n".join(p))
    print("data-pack.md written")


if __name__ == "__main__":
    build()
