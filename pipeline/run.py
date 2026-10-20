#!/usr/bin/env python3
"""GitHub Actions 진입점: 데이터셋 내려받기 → 실시간 시세 → 빌드 → site/ 폴더 생성."""
import json, os, sys, subprocess, datetime, urllib.request, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = ROOT
ST = os.path.join(W, "state")
KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)

def marcap():
    import pandas as pd
    years = [NOW.year] + ([NOW.year - 1] if NOW.month <= 4 else [])
    frames = []
    os.makedirs(os.path.join(W, ".cache"), exist_ok=True)
    for y in years:
        p = os.path.join(W, ".cache", f"marcap-{y}.parquet")
        url = f"https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{y}.parquet"
        try:
            urllib.request.urlretrieve(url, p)
        except Exception as e:
            print("marcap download fail", y, e)
            if not os.path.exists(p): continue
        frames.append(pd.read_parquet(p, columns=['Code','Name','Market','Date','Open','High','Low','Close','Volume','Amount','Changes','ChangesRatio','Marcap','Rank','Dept']))
    df = pd.concat(frames)
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    dates = sorted(df["Date"].unique())[-90:]
    df = df[df["Date"].isin(dates)]
    rows = json.loads(df.to_json(orient="records", force_ascii=False))
    with open(os.path.join(W, "marcap.json"), "w") as f: json.dump(rows, f, ensure_ascii=False)
    print("marcap rows", len(rows), "last", dates[-1])

def compose():
    ed = json.load(open(os.path.join(ST, "editorial.json")))
    au = json.load(open(os.path.join(ST, "auto.json"))) if os.path.exists(os.path.join(ST, "auto.json")) else {}
    latest = dict(ed.get("latest") or {})
    q = {x["id"]: x for x in latest.get("quotes", [])}
    for x in au.get("quotes", []): q[x["id"]] = x
    latest["quotes"] = list(q.values())
    if au.get("quotes"): latest["asOfLabel"] = NOW.strftime("%Y-%m-%d %H:%M KST") + " 자동 갱신"
    m = dict(latest=latest, picks=ed.get("picks"), picksHist=ed.get("picksHist", {}), ticks=au.get("ticks", []),
             status=au.get("status") or ed.get("status") or {})
    json.dump(m, open(os.path.join(ST, "market.json"), "w"), ensure_ascii=False)

def main():
    marcap()
    subprocess.run([sys.executable, os.path.join(W, "pipeline", "live.py"), W, os.path.join(W, "marcap.json")], check=False)
    compose()
    site = os.path.join(W, "site")
    shutil.rmtree(site, ignore_errors=True); os.makedirs(site)
    env = dict(os.environ, OUT_DIR=site)
    subprocess.run([sys.executable, os.path.join(W, "pipeline", "build.py"), W], check=True, env=env)
    shutil.copy(os.path.join(W, "index.html"), os.path.join(site, "index.html"))
    open(os.path.join(site, ".nojekyll"), "w").close()
    shutil.rmtree(os.path.join(site, "data", "state"), ignore_errors=True)

if __name__ == "__main__":
    main()
