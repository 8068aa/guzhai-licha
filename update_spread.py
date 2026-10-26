# -*- coding: utf-8 -*-
"""股债利差监测 · 每日自动更新（服务端烘焙 data.js）

数据源：
- 沪深300 收盘 + PE-TTM：中证指数官网 index-perf 接口
- 10年期国债收益率：东方财富数据中心（EMM00166466 = 中国10年国债）
合并后写入 data.js，页面优先加载。任一数据源失败即退出码 1，由工作流重试。
"""
import json, os, re, sys, datetime, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, "index.html")
OUT = os.path.join(BASE, "data.js")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def get(url, headers=None, timeout=30):
    h = dict(UA); h.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout).read()


def norm_date(s):
    s = (s or "").strip().replace("/", "-").replace(".", "-")
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def fetch_csindex():
    t = datetime.date.today().strftime("%Y%m%d")
    url = ("https://www.csindex.com.cn/csindex-home/perf/index-perf?"
           f"indexCode=000300&startDate=20050101&endDate={t}")
    j = json.loads(get(url, {"Referer": "https://www.csindex.com.cn/"}).decode("utf-8", "ignore"))
    out = {}
    for r in j.get("data") or []:
        d = norm_date(r.get("tradeDate"))
        c, p = r.get("close"), r.get("peg")
        if d and c and p:
            out[d] = (float(c), float(p))
    if len(out) < 1000:
        raise RuntimeError(f"csindex too few: {len(out)}")
    return out


def fetch_em_yields():
    """东方财富 中国10年期国债收益率（EMM00166466），分页取全历史"""
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=SOLAR_DATE"
           "&sortTypes=1&pageSize=500&pageNumber={n}&reportName=RPTA_WEB_TREASURYYIELD"
           "&columns=ALL&source=WEB&client=WEB")
    out = {}
    n = 1
    while n < 40:
        j = json.loads(get(url.format(n=n), {"Referer": "https://data.eastmoney.com/"}).decode())
        rows = (j.get("result") or {}).get("data") or []
        if not rows:
            break
        for r in rows:
            d = norm_date(r.get("SOLAR_DATE"))
            v = r.get("EMM00166466")
            if d and v is not None:
                out[d] = round(float(v), 4)
        if len(rows) < 500:
            break
        n += 1
    if len(out) < 500:
        raise RuntimeError(f"em yields too few: {len(out)}")
    return out


def embed_monthly():
    m = re.search(r"const EMBED = (\{.*?\});", open(HTML, encoding="utf-8").read(), re.S)
    return json.loads(m.group(1))["monthly"] if m else []


def main():
    cs = fetch_csindex()
    yld = fetch_em_yields()
    M = {}
    for d, (c, p) in cs.items():
        M[d] = {"close": c, "pe": p}
    for d, y in yld.items():
        M.setdefault(d, {})["yld"] = y
    # 内置月度估算补缺
    for r in embed_monthly():
        e = M.setdefault(r[0], {})
        e.setdefault("close", r[1]); e.setdefault("pe", r[2]); e.setdefault("yld", r[3])
    # 收益率前向填充
    last = None
    rows = []
    for d in sorted(M):
        e = M[d]
        if e.get("yld") is not None:
            last = e["yld"]
        elif last is not None:
            e["yld"] = last
        if e.get("close") and e.get("pe") and e.get("yld"):
            rows.append([d, round(e["close"], 2), round(e["pe"], 2), round(e["yld"], 4)])
    if len(rows) < 1000:
        raise RuntimeError(f"merged too few: {len(rows)}")
    asof = rows[-1][0]
    # 债券当日数据已发布而沪深300仍停在前一日，说明指数接口尚未更新。
    # 不能把旧数据当作成功结果写出，交给 GitHub Actions 稍后重试。
    today_cn = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()
    if today_cn in yld and today_cn not in cs:
        raise RuntimeError(f"沪深300尚未发布 {today_cn} 数据，当前仅到 {max(cs)}")
    spread = round(100 / rows[-1][2] - rows[-1][3], 2)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write('window.BAKED={"asof":"%s","rows":%s};\n'
                % (asof, json.dumps(rows, separators=(",", ":"))))
    print(f"OK asof={asof} rows={len(rows)} 沪深300={rows[-1][1]} PE={rows[-1][2]} "
          f"10Y={rows[-1][3]} 利差={spread}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)
