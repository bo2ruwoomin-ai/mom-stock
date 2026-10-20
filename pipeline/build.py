#!/usr/bin/env python3
"""장세 관제실 데이터 빌드.
입력(W=작업 폴더):
  W/marcap.json        FinanceData/marcap 2026 parquet을 dump.mjs로 푼 행 목록(전 종목 일봉)
  W/state/curated.json {code: 문서} 미국·ETF·지수·직접 관리 종목(Claude가 쓴 ai 포함)
  W/state/market.json  {latest, picks, status, ticks[], picksHist{date:picks}}
  W/state/live.json    (선택) {code: {price,o,h,l,v,chgPct,asOf,src,final}} 국내 장중/당일 시세 보강
출력(W/out):
  data/index.json      전 종목 요약(검색·목록·스크린용)
  data/h/<n>.json      상세(일봉 60행·AI 가격대) 96개 묶음
  data/market.json     시장 정보·추천·스크린·장중 기록
  data/state/*.json    다음 실행이 다시 읽을 상태 파일
사용: python3 build.py W
"""
import json, os, sys, math, statistics, datetime, shutil

W = sys.argv[1] if len(sys.argv) > 1 else "."
OUT = os.environ.get("OUT_DIR") or os.path.join(W, "out")
NCH = 96
KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)

def chunk_of(code):
    h = 0
    for ch in code:
        h = (h * 31 + ord(ch)) % 100003
    return h % NCH

def tick(p, ccy):
    if ccy != "KRW":
        return round(p, 2)
    t = 1000 if p >= 500000 else 500 if p >= 200000 else 100 if p >= 50000 else 50 if p >= 20000 else 10 if p >= 5000 else 5 if p >= 1000 else 1
    return int(round(p / t) * t)

def fm(v, ccy):
    if v is None: return "—"
    return f"{v:,.0f}원" if ccy == "KRW" else f"${v:,.2f}" if ccy == "USD" else f"{v:,.2f}"

def tech(hist, ccy, price=None):
    r = sorted(hist, key=lambda x: x["d"])
    r = [x for x in r if x.get("c") and x.get("h") and x.get("l")]
    if len(r) < 21: return None
    C = [x["c"] for x in r]; H = [x["h"] for x in r]; L = [x["l"] for x in r]; V = [x.get("v") or 0 for x in r]
    tr = [H[0] - L[0]] + [max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(1, len(r))]
    atr = sum(tr[-14:]) / 14
    ma5 = sum(C[-5:]) / 5; ma20 = sum(C[-20:]) / 20
    g = sum(max(0, C[i] - C[i - 1]) for i in range(len(C) - 14, len(C)))
    l = sum(max(0, C[i - 1] - C[i]) for i in range(len(C) - 14, len(C)))
    rsi = 100 if l == 0 else 100 - 100 / (1 + g / l)
    s20 = min(L[-20:]); r20 = max(H[-20:])
    c = price if price else C[-1]
    if atr <= 0: return None
    lo = max(s20, c - 1.5 * atr); hi = c - 0.5 * atr
    if lo >= hi: lo = hi - 0.5 * atr
    t1 = min(ma20, c + 2.5 * atr) if c < ma20 else c + 1.5 * atr
    t2 = min(r20, c + 4 * atr)
    if t2 <= t1 * 1.005: t2 = t1 + atr
    stop = min(s20, lo) - 0.5 * atr
    mid = (lo + hi) / 2
    rr = (t1 - mid) / (mid - stop) if mid > stop else 0
    vavg = sum(V[-21:-1]) / 20 if len(V) >= 21 else None
    lowd = min(r[-20:], key=lambda x: x["l"])["d"]; highd = max(r[-20:], key=lambda x: x["h"])["d"]
    return dict(c=c, atr=atr, atrPct=atr / c * 100, ma5=ma5, ma20=ma20, rsi=rsi, s20=s20, r20=r20,
                lo=tick(lo, ccy), hi=tick(hi, ccy), t1=tick(t1, ccy), t2=tick(t2, ccy), stop=tick(stop, ccy), rr=rr,
                dd20=(c / r20 - 1) * 100, chg5=(C[-1] / C[-6] - 1) * 100, chg20=(C[-1] / C[-21] - 1) * 100,
                volRatio=(V[-1] / vavg) if vavg else None, lowd=lowd, highd=highd, last=r[-1]["d"])

METHOD = "매수 구간 = max(20일 저가, 종가−1.5×ATR) ~ 종가−0.5×ATR · 1차 목표 = min(20일선, 종가+2.5×ATR) · 2차 목표 = min(20일 고가, 종가+4×ATR) · 손절 = min(20일 저가, 매수 하단)−0.5×ATR"

def clip(x, a, b): return max(a, min(b, x))

def base_score(t, lev, flag, amt20=None, ytd_dd=None):
    """0~100 관심도. 과매도·손익비·추세·변동성·거래대금·연중 낙폭을 연속값으로 합산."""
    sc = 50.0
    sc += clip((40 - t["rsi"]) / 40 * 14, 0, 14) - clip((t["rsi"] - 65) / 35 * 8, 0, 8)
    sc += clip((t["rr"] - 1.5) * 4, -10, 12)
    sc += 5 if (t["c"] > t["ma20"] and t["ma5"] > t["ma20"]) else -3 if (t["c"] < t["ma20"] and t["ma5"] < t["ma20"]) else 0
    sc -= clip((t["atrPct"] - 4) * 2, 0, 10)
    if amt20: sc += clip(math.log10(max(amt20, 1) / 1e10) * 2, -4, 4)
    if ytd_dd is not None and ytd_dd < -50: sc -= 4
    if (t.get("volRatio") or 0) >= 2 and t["chg5"] > 0: sc += 3
    if lev: sc -= 15
    if flag: sc -= 20
    return int(clip(round(sc), 5, 95))

def auto_ai(t, ccy, lev, note, flag, stamp, amt20=None, ytd_dd=None):
    sc = base_score(t, lev, flag, amt20, ytd_dd)
    rsi, rr = t["rsi"], t["rr"]
    if flag: stance = "주의 종목 · 관망"
    elif lev: stance = "단기 매매용 · 장기 보유 주의"
    elif rr < 1.5: stance = "추격 자제 · 눌림 대기" if t["c"] > t["ma20"] else "관망"
    elif rsi < 30: stance = "반등 관찰"
    elif t["c"] > t["ma20"] and t["ma5"] > t["ma20"]: stance = "추세 유지 · 눌림 매수 관찰"
    else: stance = "분할 매수 관찰"
    reasons = []
    if rsi < 35: reasons.append(f"RSI(과열·과매도 지표) {rsi:.1f}로 많이 내린 상태 — 짧은 반등 여지")
    elif rsi > 65: reasons.append(f"RSI {rsi:.1f}로 과열에 가까움")
    else: reasons.append(f"RSI {rsi:.1f}로 보통 구간")
    reasons.append(f"20일 고점({t['highd'][5:].replace('-','/')}, {fm(t['r20'],ccy)}) 대비 {t['dd20']:+.1f}%, 20일 저점은 {t['lowd'][5:].replace('-','/')} {fm(t['s20'],ccy)}")
    reasons.append(("5일 평균이 20일 평균 위 — 단기 흐름이 올라가는 중" if t["ma5"] > t["ma20"] else "5일 평균이 20일 평균 아래 — 단기 흐름이 내려가는 중") + f" (20일 평균 {fm(t['ma20'],ccy)})")
    if t.get("volRatio") and t["volRatio"] >= 2: reasons.append(f"거래량이 20일 평균의 {t['volRatio']:.1f}배 — 관심이 몰림")
    reasons.append(f"손익비 {rr:.2f} (1차 목표까지 기대폭 ÷ 손절폭)")
    risks = [f"하루 평균 변동폭 {t['atrPct']:.1f}% — {fm(t['stop'],ccy)} 아래로 내려가면 하락이 길어질 수 있음"]
    if t["c"] < t["ma20"]: risks.append("20일 평균 아래라 반등이 막힐 수 있음")
    if lev: risks.insert(0, f"{note or '레버리지·인버스'} 상품 — 날마다 배수를 맞추므로 오래 들고 있으면 기초지수보다 손실이 커질 수 있음")
    if flag: risks.insert(0, f"거래소 지정 '{flag}' — 상장폐지·급변동 위험이 커서 초보자에게 맞지 않음")
    return dict(stance=stance, score=sc, horizon="며칠~2주" if lev else "1~4주",
                summary=f"{fm(t['c'],ccy)} · 5일 {t['chg5']:+.1f}% · 20일 {t['chg20']:+.1f}%. 살 만한 구간 {fm(t['lo'],ccy)}~{fm(t['hi'],ccy)}, 1차 목표 {fm(t['t1'],ccy)}.",
                reasons=reasons, risks=risks,
                checks=[f"{fm(t['s20'],ccy)}(20일 저점)을 지키는지", f"{fm(t['ma20'],ccy)}(20일 평균)을 되찾는지"],
                buy=[t["lo"], t["hi"]], targets=[t["t1"], t["t2"]], stop=t["stop"], rr=round(rr, 2),
                method=METHOD, updatedAt=stamp, by="규칙 계산 (Claude가 마감 때 주요 종목은 다시 작성)")

def spark(hist):
    r = sorted(hist, key=lambda x: x["d"])[-20:]
    if len(r) < 2: return ""
    v = [x["c"] for x in r]; lo, hi = min(v), max(v); rg = (hi - lo) or 1
    al = "0123456789abcdefghijklmnopqrstuvwxyz"
    return "".join(al[min(35, int((x - lo) / rg * 35 + 0.5))] for x in v)

def load(p, default):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return default

def main():
    stamp = NOW.strftime("%Y-%m-%d %H:%M KST")
    rows = load(os.path.join(W, "marcap.json"), [])
    curated = load(os.path.join(W, "state", "curated.json"), {})
    market = load(os.path.join(W, "state", "market.json"), {})
    live = load(os.path.join(W, "state", "live.json"), {})

    # ---- KRX universe from marcap ----
    by = {}
    for r in rows:
        if r.get("Market") not in ("KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"): continue
        dept = r.get("Dept") or ""
        if "SPAC" in dept: continue
        by.setdefault(r["Code"], []).append(r)
    docs = {}
    for code, rs in by.items():
        rs.sort(key=lambda x: x["Date"])
        if not any(x.get("Close") for x in rs): continue
        last = rs[-1]
        hist = [dict(d=x["Date"], o=x["Open"], h=x["High"], l=x["Low"], c=x["Close"], v=x["Volume"]) for x in rs if x["Close"]]
        hist = [h for h in hist if h["c"] and h["h"] and h["l"]]
        if not hist: continue
        for h in hist:
            if not h["o"]: h["o"] = h["c"]
        dept = last.get("Dept") or ""
        flag = "관리종목" if "관리" in dept else "투자주의환기" if "투자주의" in dept else ""
        mk = "코스피" if last["Market"] == "KOSPI" else "코스닥"
        amt20 = statistics.mean([x.get("Amount") or 0 for x in rs[-20:]])
        docs[code] = dict(code=code, name=last["Name"], market=mk, ccy="KRW", kind="stock",
                          cat=mk,
                          aliases=[code], history=hist[-60:], mcap=last.get("Marcap"), rank=last.get("Rank"), amt20=amt20,
                          ytdHigh=max([x["High"] for x in rs if x["High"]] or [0]), ytdLow=min([x["Low"] for x in rs if x["Low"]] or [0]),
                          flag=flag, src="KRX 일봉(FinanceData/marcap)", asOf=f"{last['Date']} 종가")
    # ---- merge curated (KR ETFs, US, indices, Claude-written KR) ----
    for code, c in curated.items():
        if code in docs and c.get("ccy") == "KRW" and c.get("kind") == "stock":
            d = docs[code]
            mh = {h["d"]: h for h in d["history"]}
            for h in c.get("history", []):
                if h["d"] not in mh: mh[h["d"]] = h
            d["history"] = sorted(mh.values(), key=lambda x: x["d"])[-60:]
            for k in ("name", "aliases", "note", "ai", "per", "pbr", "high52", "low52"):
                if c.get(k): d[k] = c[k] if k != "aliases" else sorted(set(d["aliases"] + c[k]))
            if c.get("asOf") and c["history"] and max(h["d"] for h in c["history"]) >= max(h["d"] for h in d["history"]):
                d["asOf"] = c["asOf"]; d["src"] = c.get("src", d["src"]) + " + KRX 일봉"
            d["curated"] = True
        else:
            docs[code] = dict(c, curated=True)
    # ---- live overrides (KRX intraday / today close) ----
    for code, q in live.items():
        d = docs.get(code)
        if not d or not q.get("price"): continue
        d["price"] = q["price"]; d["asOf"] = q.get("asOf", d.get("asOf")); d["src"] = q.get("src", d.get("src"))
        if q.get("prevClose"): d["prevClose"] = q["prevClose"]; d["livePrev"] = True
        if q.get("final") and q.get("o") and q.get("h") and q.get("l") and q.get("date"):
            hm = {h["d"]: h for h in d["history"]}
            hm[q["date"]] = dict(d=q["date"], o=q["o"], h=q["h"], l=q["l"], c=q["price"], v=q.get("v") or 0)
            d["history"] = sorted(hm.values(), key=lambda x: x["d"])[-60:]
    # ---- compute ----
    newest = {}; cnt = {}
    for d in docs.values():
        if d.get("history"):
            ld = max(h["d"] for h in d["history"]); d["lastDate"] = ld
            newest[d["ccy"]] = max(newest.get(d["ccy"], ""), ld)
            cnt.setdefault(d["ccy"], {}).setdefault(ld, 0); cnt[d["ccy"]][ld] += 1
    # baseline = the date most stocks of that currency share (bulk dataset day), so a few fresher curated rows don't mark everyone stale
    base = {ccy: max(c.items(), key=lambda kv: (kv[1], kv[0]))[0] for ccy, c in cnt.items()}
    index = []
    for code, d in docs.items():
        h = sorted(d.get("history") or [], key=lambda x: x["d"])
        if h and not d.get("pending"):
            lastc = h[-1]["c"]
            if "price" not in d or not d.get("curated") and code not in live: d["price"] = lastc
            prev = h[-2]["c"] if len(h) >= 2 else None
            if d.get("livePrev"): pass
            elif d["price"] == lastc and prev: d["prevClose"] = prev
            elif d.get("prevClose") is None: d["prevClose"] = lastc
            if d.get("prevClose"):
                d["chg"] = d["price"] - d["prevClose"]; d["chgPct"] = (d["price"] / d["prevClose"] - 1) * 100
            ld = d.get("lastDate")
            days = (datetime.date.fromisoformat(base[d["ccy"]]) - datetime.date.fromisoformat(ld)).days if ld else 99
            d["stale"] = days >= 3 and d["kind"] != "index"
        t = tech(h, d["ccy"], d.get("price")) if h and d["kind"] != "index" and not d.get("pending") else None
        if t:
            claude_ai = d.get("ai") and str(d["ai"].get("by", "")).startswith("Claude") and "규칙" not in str(d["ai"].get("by", "")) and str(d["ai"].get("updatedAt", ""))[:10] >= (d.get("lastDate") or "")
            if not claude_ai:
                ytd_dd = (d["price"] / d["ytdHigh"] - 1) * 100 if d.get("ytdHigh") else None
                d["ai"] = auto_ai(t, d["ccy"], d["kind"] == "lev", d.get("note"), d.get("flag"), stamp, d.get("amt20"), ytd_dd)
            d["score"] = d["ai"]["score"]
            d["t"] = dict(rsi=round(t["rsi"], 1), atrPct=round(t["atrPct"], 2), ma5=t["ma5"], ma20=t["ma20"], s20=t["s20"], r20=t["r20"],
                          dd20=round(t["dd20"], 2), chg5=round(t["chg5"], 2), chg20=round(t["chg20"], 2), volRatio=round(t["volRatio"], 2) if t["volRatio"] else None)
        a = d.get("ai") or {}
        it = dict(c=code, n=d["name"], m=d.get("market"), k=d["kind"], g=d.get("cat"), y=d["ccy"])
        if d.get("pending"): it["w"] = 1
        else:
            it.update(p=d.get("price"), x=round(d["chgPct"], 2) if d.get("chgPct") is not None else None, d=d.get("lastDate"))
            if a: it.update(s=a.get("score"), st=a.get("stance"), b=a.get("buy"), t1=(a.get("targets") or [None])[0], sl=a.get("stop"), rr=a.get("rr"))
            if d.get("t"): it.update(r=d["t"]["rsi"], v=d["t"]["volRatio"], c5=d["t"]["chg5"], c20=d["t"]["chg20"])
            it["sp"] = spark(h)
            if d.get("stale"): it["o"] = 1
        if d.get("mcap"): it["mc"] = round(d["mcap"] / 1e8)
        if d.get("rank"): it["rk"] = d["rank"]
        if d.get("flag"): it["f"] = d["flag"]
        if d.get("note"): it["nt"] = d["note"]
        al = [x for x in d.get("aliases", []) if x not in (code, d["name"])]
        if al: it["al"] = al
        index.append(it)
    # ---- screens ----
    def pool(min_mc=1000, need_fresh=True):
        out = []
        for d in docs.values():
            if d["ccy"] != "KRW" or d["kind"] != "stock" or d.get("pending") or not d.get("t"): continue
            if d.get("flag"): continue
            if (d.get("mcap") or 0) < min_mc * 1e8: continue
            if d.get("price", 0) < 1000: continue
            if need_fresh and d.get("stale"): continue
            if (d.get("amt20") or 3e10) < 3e9: continue
            out.append(d)
        return out
    def item(d, why):
        a = d.get("ai", {})
        return dict(code=d["code"], name=d["name"], market=d.get("market"), price=d.get("price"), chgPct=round(d.get("chgPct") or 0, 2),
                    score=a.get("score"), stance=a.get("stance"), buy=a.get("buy"), t1=(a.get("targets") or [None])[0], stop=a.get("stop"), rr=a.get("rr"), why=why)
    P = pool(1000)
    screens = []
    for mk in ("코스피", "코스닥"):
        c = sorted([d for d in P if d["market"] == mk and (d["ai"]["rr"] or 0) >= 2 and d["mcap"] >= 3000e8], key=lambda d: -d["ai"]["score"])[:20]
        screens.append(dict(id="top-" + ("kospi" if mk == "코스피" else "kosdaq"), title=f"{mk} AI 점수 상위", desc="시총 3천억 이상·거래 활발·손익비 2 이상 중 점수 높은 순",
                            items=[item(d, f"점수 {d['ai']['score']} · RSI {d['t']['rsi']} · 손익비 {d['ai']['rr']}") for d in c]))
    c = sorted([d for d in P if d["t"]["rsi"] < 30 and d["mcap"] >= 5000e8], key=lambda d: d["t"]["rsi"])[:20]
    screens.append(dict(id="oversold", title="많이 내려 반등을 노려볼 만한 곳", desc="시총 5천억 이상 중 RSI 30 미만(과매도)",
                        items=[item(d, f"RSI {d['t']['rsi']} · 20일 고점 대비 {d['t']['dd20']:+.1f}%") for d in c]))
    c = sorted([d for d in P if (d["t"]["volRatio"] or 0) >= 2.5 and (d.get("chgPct") or 0) > 0], key=lambda d: -d["t"]["volRatio"])[:20]
    screens.append(dict(id="volume", title="거래량이 갑자기 늘어난 곳", desc="오늘 거래량이 20일 평균의 2.5배 이상이면서 오른 종목",
                        items=[item(d, f"거래량 {d['t']['volRatio']:.1f}배 · 오늘 {d['chgPct']:+.2f}%") for d in c]))
    c = sorted([d for d in P if d["price"] >= 0.97 * d["ytdHigh"] and d["t"]["ma5"] > d["t"]["ma20"] and d["mcap"] >= 3000e8], key=lambda d: -d["mcap"])[:20]
    screens.append(dict(id="highs", title="올해 최고가 근처에서 힘이 센 곳", desc="올해 최고가의 97% 이상 + 단기 평균이 올라가는 중(추격 주의)",
                        items=[item(d, f"올해 최고가 {d['ytdHigh']:,.0f}원의 {d['price']/d['ytdHigh']*100:.0f}%") for d in c]))
    G = [d for d in P if d.get("chgPct") is not None]
    screens.append(dict(id="gainers", title="많이 오른 종목", desc="시총 1천억 이상", items=[item(d, f"{d['chgPct']:+.2f}%") for d in sorted(G, key=lambda d: -d["chgPct"])[:20]]))
    screens.append(dict(id="losers", title="많이 내린 종목", desc="시총 1천억 이상", items=[item(d, f"{d['chgPct']:+.2f}%") for d in sorted(G, key=lambda d: d["chgPct"])[:20]]))
    big = sorted([d for d in docs.values() if d["ccy"] == "KRW" and d["kind"] == "stock" and d.get("mcap")], key=lambda d: -d["mcap"])[:30]
    screens.append(dict(id="blue", title="시가총액 상위 30 한눈에", desc="국내 큰 회사들의 오늘 상태",
                        items=[item(d, f"시총 {d['mcap']/1e12:,.1f}조원") for d in big if d.get("ai")]))
    market["screens"] = screens
    market["universe"] = dict(total=len(index), kospi=sum(1 for i in index if i.get("m") == "코스피"), kosdaq=sum(1 for i in index if i.get("m") == "코스닥"),
                              krxDate=base.get("KRW"), krxNewest=newest.get("KRW"), usDate=base.get("USD"), builtAt=stamp)
    # candidate list for Claude's daily picks
    cand = sorted([d for d in pool(3000) if d["ai"]["rr"] >= 2], key=lambda d: -d["ai"]["score"])[:40]
    market["candidates"] = [dict(code=d["code"], name=d["name"], market=d["market"], score=d["ai"]["score"], rr=d["ai"]["rr"], rsi=d["t"]["rsi"],
                                 chg5=d["t"]["chg5"], chg20=d["t"]["chg20"], mcapJo=round(d["mcap"] / 1e12, 2)) for d in cand]
    # ---- write ----
    os.makedirs(os.path.join(OUT, "data", "h"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "data", "state"), exist_ok=True)
    chunks = [dict() for _ in range(NCH)]
    keep = ("code", "name", "market", "ccy", "kind", "cat", "aliases", "note", "pending", "pendingNote", "price", "prevClose", "chg", "chgPct", "asOf", "src",
            "high52", "low52", "ytdHigh", "ytdLow", "per", "pbr", "mcap", "rank", "flag", "history", "ai", "score", "t", "stale", "lastDate")
    for code, d in docs.items():
        chunks[chunk_of(code)][code] = {k: d[k] for k in keep if k in d}
    for i, ch in enumerate(chunks):
        with open(os.path.join(OUT, "data", "h", f"{i}.json"), "w") as f: json.dump(ch, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT, "data", "index.json"), "w") as f:
        json.dump(dict(builtAt=stamp, krxDate=base.get("KRW"), usDate=base.get("USD"), n=len(index), items=index), f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT, "data", "market.json"), "w") as f: json.dump(market, f, ensure_ascii=False, separators=(",", ":"))
    cur_out = {code: {k: d[k] for k in keep if k in d} for code, d in docs.items() if d.get("curated")}
    with open(os.path.join(OUT, "data", "state", "curated.json"), "w") as f: json.dump(cur_out, f, ensure_ascii=False)
    with open(os.path.join(OUT, "data", "state", "market.json"), "w") as f: json.dump(market, f, ensure_ascii=False)
    # brief for Claude's daily picks (small, committed by the workflow)
    def bri(d):
        a, t = d.get("ai") or {}, d.get("t") or {}
        return dict(code=d["code"], name=d["name"], market=d.get("market"), ccy=d["ccy"], kind=d["kind"], cat=d.get("cat"), flag=d.get("flag"),
                    price=d.get("price"), chgPct=d.get("chgPct"), asOf=d.get("asOf"), lastDate=d.get("lastDate"), stale=d.get("stale"),
                    mcapJo=round(d["mcap"] / 1e12, 2) if d.get("mcap") else None,
                    ai={k: a.get(k) for k in ("stance", "score", "buy", "targets", "stop", "rr")},
                    t={k: t.get(k) for k in ("rsi", "dd20", "chg5", "chg20", "ma20", "volRatio")})
    want = [d["code"] for d in cand]
    want += [c for c, d in docs.items() if d["ccy"] == "USD" and d["kind"] != "lev" and d.get("ai") and d.get("t")]
    want += [c for c, d in docs.items() if d["ccy"] == "KRW" and d["kind"] == "stock" and d.get("mcap") and d.get("ai") and d.get("t")
             and d.get("rank") and d["rank"] <= 15]
    for p in (market.get("picksHist") or {}).values():
        want += [x["code"] for x in (p or {}).get("items", [])]
    seen, items = set(), []
    for c in want:
        if c in docs and c not in seen and docs[c].get("ai"):
            seen.add(c); items.append(bri(docs[c]))
    brief = dict(builtAt=stamp, krxDate=newest.get("KRW"), usDate=newest.get("USD"), quotes=(market.get("latest") or {}).get("quotes", []),
                 universe=market["universe"], items=items)
    with open(os.path.join(W, "state", "brief.json"), "w") as f: json.dump(brief, f, ensure_ascii=False, indent=0)
    print(f"built {len(index)} items (KRX {len(by)}), curated {len(cur_out)}, krxDate {newest.get('KRW')}, usDate {newest.get('USD')}")
    sz = sum(os.path.getsize(os.path.join(OUT, "data", "h", f"{i}.json")) for i in range(NCH))
    print("index KB", os.path.getsize(os.path.join(OUT, "data", "index.json")) // 1024, "chunks KB", sz // 1024)

if __name__ == "__main__":
    main()
