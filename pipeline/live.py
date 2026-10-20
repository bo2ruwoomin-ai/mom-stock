#!/usr/bin/env python3
"""GitHub Actions에서 실행: 실시간에 가까운 시세를 모아 state/ 파일을 갱신한다.

- 국내 전 종목: 네이버 금융 polling API (현재가·시가·고가·저가·거래량·전일 종가)
- 지수·미국 주식·ETF·환율·유가·금리: Yahoo Finance chart API (실패 시 stooq 일봉)
- 결과: state/live.json(국내 종목 오늘 시세), state/curated.json(미국·ETF·지수 일봉/현재가 갱신),
        state/auto.json(시장 지표 quotes, 장중 기록 ticks, 상태 status)
사용: python3 pipeline/live.py <작업폴더 W> <marcap.json 경로>
"""
import json, os, sys, time, datetime, urllib.request, urllib.parse, re

W = sys.argv[1]
MARCAP = sys.argv[2] if len(sys.argv) > 2 else os.path.join(W, "marcap.json")
KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)
TODAY = NOW.strftime("%Y-%m-%d")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "application/json,text/plain,*/*", "Referer": "https://finance.naver.com/"}
LOG = []

def log(*a):
    s = " ".join(str(x) for x in a); LOG.append(s); print(s, flush=True)

def get(url, timeout=15, raw=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    if raw: return b
    for enc in ("utf-8", "euc-kr", "cp949"):
        try: return b.decode(enc)
        except UnicodeDecodeError: pass
    return b.decode("utf-8", "replace")

def num(x):
    if x is None: return None
    if isinstance(x, (int, float)): return x
    s = str(x).replace(",", "").replace("+", "").strip()
    try: return float(s) if "." in s else int(s)
    except ValueError: return None

def load(p, d):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d

def save(p, o):
    with open(p, "w") as f: json.dump(o, f, ensure_ascii=False, separators=(",", ":"))

# ---------- 국내: 네이버 ----------
def naver_batch(codes):
    """여러 종목 실시간. 새 API → 옛 API 순서로 시도."""
    out = {}
    try:
        j = json.loads(get("https://polling.finance.naver.com/api/realtime/domestic/stock/" + ",".join(codes)))
        for d in j.get("datas") or []:
            c = d.get("itemCode")
            price = num(d.get("closePrice"))
            if not c or not price: continue
            prev = None
            chg = num(d.get("compareToPreviousClosePrice"))
            if chg is not None:
                # 하락이면 compareToPreviousPrice.code 가 5/4 (하락/하한)
                code_ = str((d.get("compareToPreviousPrice") or {}).get("code", ""))
                if code_ in ("4", "5") and chg > 0: chg = -chg
                prev = price - chg
            out[c] = dict(price=price, prevClose=prev, o=num(d.get("openPrice")), h=num(d.get("highPrice")), l=num(d.get("lowPrice")),
                          v=num(d.get("accumulatedTradingVolume")), status=d.get("marketStatus"), at=d.get("localTradedAt"))
        if out: return out
    except Exception as e:
        log("naver new api fail", type(e).__name__, str(e)[:120])
    try:
        j = json.loads(get("https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:" + ",".join(codes)))
        for a in (j.get("result") or {}).get("areas") or []:
            for d in a.get("datas") or []:
                c = d.get("cd"); price = num(d.get("nv"))
                if not c or not price: continue
                out[c] = dict(price=price, prevClose=num(d.get("sv")), o=num(d.get("ov")), h=num(d.get("hv")), l=num(d.get("lv")),
                              v=num(d.get("aq")), status=d.get("ms"), at=None)
    except Exception as e:
        log("naver old api fail", type(e).__name__, str(e)[:120])
    return out

def naver_index(sym):
    for url in (f"https://polling.finance.naver.com/api/realtime/domestic/index/{sym}", f"https://m.stock.naver.com/api/index/{sym}/basic"):
        try:
            j = json.loads(get(url))
            d = (j.get("datas") or [j])[0]
            price = num(d.get("closePrice")); chg = num(d.get("compareToPreviousClosePrice")); pct = num(d.get("fluctuationsRatio"))
            code_ = str((d.get("compareToPreviousPrice") or {}).get("code", ""))
            if chg is not None and code_ in ("4", "5") and chg > 0: chg = -chg
            if pct is not None and code_ in ("4", "5") and pct > 0: pct = -pct
            if price: return dict(price=price, chg=chg, chgPct=pct, o=num(d.get("openPrice")), h=num(d.get("highPrice")), l=num(d.get("lowPrice")), status=d.get("marketStatus"))
        except Exception as e:
            log("naver index fail", sym, url, type(e).__name__)
    return None

# ---------- 해외: Yahoo / stooq ----------
def yahoo(sym, rng="3mo"):
    for host in ("query1", "query2"):
        try:
            j = json.loads(get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range={rng}&interval=1d&includePrePost=false"))
            r = j["chart"]["result"][0]; m = r["meta"]; q = r["indicators"]["quote"][0]
            hist = []
            for i, ts in enumerate(r.get("timestamp") or []):
                o, h, l, c, v = (q.get(k, [None])[i] if i < len(q.get(k, [])) else None for k in ("open", "high", "low", "close", "volume"))
                if None in (o, h, l, c): continue
                d = datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(seconds=m.get("gmtoffset", 0)))).strftime("%Y-%m-%d")
                hist.append(dict(d=d, o=round(o, 4), h=round(h, 4), l=round(l, 4), c=round(c, 4), v=int(v or 0)))
            price = m.get("regularMarketPrice"); prev = m.get("chartPreviousClose") if rng == "1d" else (hist[-2]["c"] if len(hist) >= 2 else m.get("previousClose"))
            # 마지막 행이 오늘 장중이면 price 기준 prev = 그 전날 종가
            if hist and price and abs(hist[-1]["c"] - price) / price < 0.02: prev = hist[-2]["c"] if len(hist) >= 2 else prev
            t = m.get("regularMarketTime")
            at = datetime.datetime.fromtimestamp(t, KST).strftime("%Y-%m-%d %H:%M KST") if t else None
            return dict(price=price, prevClose=prev, hist=hist, at=at, state=m.get("marketState"))
        except Exception as e:
            log("yahoo fail", sym, host, type(e).__name__, str(e)[:80])
    return None

def stooq(sym):
    try:
        t = get(f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d")
        rows = [l.split(",") for l in t.strip().splitlines()[1:]]
        hist = [dict(d=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]), v=int(float(r[5] or 0))) for r in rows[-70:] if len(r) >= 6 and r[4] not in ("", "null")]
        if len(hist) < 2: return None
        return dict(price=hist[-1]["c"], prevClose=hist[-2]["c"], hist=hist, at=f"{hist[-1]['d']} 종가", state=None)
    except Exception as e:
        log("stooq fail", sym, type(e).__name__)
        return None

YQ = [("KOSPI", "^KS11", "코스피", "KR", 2, ""), ("KOSDAQ", "^KQ11", "코스닥", "KR", 2, ""), ("DJI", "^DJI", "다우", "US", 2, ""), ("SPX", "^GSPC", "S&P 500", "US", 2, ""),
      ("IXIC", "^IXIC", "나스닥", "US", 2, ""), ("SOX", "^SOX", "필라델피아 반도체", "US", 2, ""), ("VIX", "^VIX", "VIX(공포지수)", "VOL", 2, ""),
      ("USDKRW", "KRW=X", "원·달러", "FX", 2, "원"), ("WTI", "CL=F", "WTI 유가", "CMD", 2, "$"), ("US10Y", "^TNX", "미 국채 10년", "RATE", 3, "%"), ("GOLD", "GC=F", "금", "CMD", 1, "$")]

def main():
    st = os.path.join(W, "state")
    curated = load(os.path.join(st, "curated.json"), {})
    auto = load(os.path.join(st, "auto.json"), {})
    rows = load(MARCAP, [])
    # 최신 거래일의 국내 종목 목록(시총 순)
    last = {}
    for r in rows:
        if r.get("Market") in ("KOSPI", "KOSDAQ", "KOSDAQ GLOBAL") and "SPAC" not in (r.get("Dept") or ""):
            if r["Code"] not in last or r["Date"] > last[r["Code"]]["Date"]: last[r["Code"]] = r
    maxd = max((r["Date"] for r in last.values()), default="")
    codes = sorted([c for c, r in last.items() if r["Date"] >= (datetime.date.fromisoformat(maxd) - datetime.timedelta(days=10)).isoformat()] if maxd else [],
                   key=lambda c: -(last[c].get("Marcap") or 0))
    log("KRX codes", len(codes), "marcap last date", maxd)

    m = NOW.hour * 60 + NOW.minute; wk = NOW.weekday() < 5
    kr_open = wk and 540 <= m <= 935
    kr_after = wk and m > 935
    live = {}
    if wk and m >= 525:  # 08:45 이후 평일이면 국내 시세 수집(마감 뒤에도 당일 확정값)
        for i in range(0, len(codes), 80):
            part = naver_batch(codes[i:i + 80])
            live.update(part)
            time.sleep(0.25)
        log("naver live", len(live))
    lv = {}
    for c, q in live.items():
        final = kr_after or (q.get("status") in ("CLOSE",) and m > 935)
        lv[c] = dict(price=q["price"], prevClose=q.get("prevClose"), o=q.get("o"), h=q.get("h"), l=q.get("l"), v=q.get("v"), date=TODAY, final=bool(final and q.get("o") and q.get("h") and q.get("l")),
                     asOf=("오늘 " + NOW.strftime("%H:%M") + " 장중" if not final else TODAY + " 종가"), src="네이버 금융 실시간 + KRX 일봉")
    save(os.path.join(st, "live.json"), lv)

    # ---- 지수·해외 지표 ----
    quotes = {q["id"]: q for q in (auto.get("quotes") or [])}
    ticks = auto.get("ticks") or []
    tlabel = NOW.strftime("%-m/%-d %H:%M"); tiso = NOW.strftime("%Y-%m-%dT%H:%M")
    def add_tick(sym, code, v, note):
        if v is None: return
        if ticks and ticks[-1].get("code") == code and ticks[-1].get("t") == tiso: return
        ticks.append(dict(sym=sym, code=code, t=tiso, v=v, label=tlabel, note=note, by="auto"))
    for qid, ysym, name, grp, digits, unit in YQ:
        y = None
        if qid in ("KOSPI", "KOSDAQ"):
            n = naver_index(qid)
            if n:
                quotes[qid] = dict(id=qid, name=name, group=grp, value=n["price"], chg=n["chg"], chgPct=n["chgPct"], digits=digits, unit=unit,
                                   at=("오늘 " + NOW.strftime("%H:%M") if kr_open else TODAY + " 마감" if kr_after else "최근 마감"), note="네이버 금융")
                if kr_open or (kr_after and m < 1000): add_tick(name, qid, n["price"], "네이버 금융")
                # 지수 일봉(curated)도 오늘 행 갱신
                d = curated.get(qid)
                if d and kr_after and n.get("o"):
                    hm = {h["d"]: h for h in d.get("history", [])}
                    hm[TODAY] = dict(d=TODAY, o=n["o"], h=n["h"], l=n["l"], c=n["price"], v=0)
                    d["history"] = sorted(hm.values(), key=lambda x: x["d"], reverse=True)[:60]
                if d:
                    d["price"] = n["price"]; d["chg"] = n["chg"]; d["chgPct"] = n["chgPct"]; d["prevClose"] = n["price"] - (n["chg"] or 0)
                    d["asOf"] = quotes[qid]["at"]; d["src"] = "네이버 금융 + Investing.com 일봉"
                continue
        y = yahoo(ysym, "5d")
        if not y or not y.get("price"): continue
        v = y["price"]
        if qid == "US10Y" and v > 20: v = v / 10
        prev = y.get("prevClose")
        if qid == "US10Y" and prev and prev > 20: prev = prev / 10
        quotes[qid] = dict(id=qid, name=name, group=grp, value=round(v, digits), chg=round(v - prev, digits + 1) if prev else None,
                           chgPct=round((v / prev - 1) * 100, 2) if prev and qid != "US10Y" else None, digits=digits, unit=unit, at=y.get("at"), note="Yahoo Finance")
    order = [q[0] for q in YQ]
    auto["quotes"] = sorted(quotes.values(), key=lambda q: order.index(q["id"]) if q["id"] in order else 99)

    # ---- curated: 미국 주식·ETF·국내 ETF ----
    us_window = (m >= 1350 or m <= 420)  # 22:30~07:00 KST
    done = 0
    for code, d in curated.items():
        if d.get("kind") == "index": continue
        if d.get("ccy") == "KRW" and code in last: continue  # 국내 상장 주식은 네이버/marcap 이 담당
        ysym = code + ".KS" if d.get("ccy") == "KRW" else code
        # 국내 ETF는 네이버로 먼저
        if d.get("ccy") == "KRW":
            q = naver_batch([code]).get(code)
            y = yahoo(ysym, "3mo")
            if q and q.get("price"):
                if y is None: y = dict(hist=[])
                y["price"] = q["price"]; y["prevClose"] = q.get("prevClose") or y.get("prevClose"); y["at"] = ("오늘 " + NOW.strftime("%H:%M")) if kr_open else TODAY + " 종가"
                if kr_after and q.get("o"):
                    y["hist"] = [h for h in y.get("hist", []) if h["d"] != TODAY] + [dict(d=TODAY, o=q["o"], h=q["h"], l=q["l"], c=q["price"], v=q.get("v") or 0)]
        else:
            y = yahoo(ysym, "3mo") or stooq(code)
        if not y or not y.get("price"):
            continue
        hm = {h["d"]: h for h in d.get("history", [])}
        for h in y.get("hist") or []: hm[h["d"]] = h
        d["history"] = sorted(hm.values(), key=lambda x: x["d"], reverse=True)[:60]
        d["price"] = y["price"]
        if y.get("prevClose"): d["prevClose"] = y["prevClose"]; d["chg"] = y["price"] - y["prevClose"]; d["chgPct"] = (y["price"] / y["prevClose"] - 1) * 100
        d["asOf"] = y.get("at") or d.get("asOf"); d["src"] = "Yahoo Finance" + (" + 네이버 금융" if d.get("ccy") == "KRW" else "")
        if d.get("pending") and len(d["history"]) >= 21: d["pending"] = False; d.pop("pendingNote", None)
        done += 1
        if d.get("ccy") == "USD" and us_window and d.get("kind") in ("stock", "etf", "lev") and (d.get("score") or 0) >= 0:
            if code in ("NVDA", "TSLA", "AAPL", "QQQ", "TQQQ", "SOXL", "SPY", "MU", "SOXX", "TSM"): add_tick(d["name"], code, y["price"], "Yahoo Finance")
        time.sleep(0.2)
    log("curated updated", done)

    # ---- 국내 주요 종목 장중 기록 ----
    if kr_open or (kr_after and m < 1000):
        top = codes[:30]
        for c in top:
            q = live.get(c)
            if q: add_tick(last[c]["Name"], c, q["price"], "네이버 금융")
    # 오래된 기록 정리(최근 12일)
    cut = (NOW - datetime.timedelta(days=12)).strftime("%Y-%m-%d")
    ticks = [t for t in ticks if str(t.get("t", ""))[:10] >= cut][-6000:]
    auto["ticks"] = ticks
    auto["status"] = dict(lastRun=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), lastRunLabel=NOW.strftime("%-m/%-d %H:%M"),
                          nextRun=("약 10분 뒤" if kr_open else "약 20분 뒤" if us_window else "다음 장 시작 전"), mode="github-actions",
                          notes="; ".join(LOG[-8:]), krLive=len(live))
    save(os.path.join(st, "auto.json"), auto)
    save(os.path.join(st, "curated.json"), curated)

if __name__ == "__main__":
    main()
