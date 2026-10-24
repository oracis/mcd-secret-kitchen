#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦麦暗号厨房 / McSecret Kitchen
基于麦当劳中国 MCP Server 的「隐藏菜单」生成器。

利用套餐内商品的可替换选项，组合出菜单上不存在的搭配，并给出暗号名。
零第三方依赖，仅用 Python 标准库。

    python mcd_secret_kitchen.py            # 本地服务 http://127.0.0.1:8849
    python mcd_secret_kitchen.py --scan     # 命令行直接输出隐藏菜单

Token 从环境变量 MCD_MCP_TOKEN 读取，永不硬编码。
"""

import json
import os
import sys
import uuid
import urllib.request
import urllib.error
import argparse
import re
from itertools import product
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MCP_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
DEFAULT_PORT = 8849


# ---------------------------------------------------------------- MCP 客户端

class McpClient:
    def __init__(self, token):
        self.token = token
        self.session_id = None

    def _post(self, payload, is_notify=False):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(MCP_URL, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                raw = resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:400]
            except Exception:
                pass
            raise RuntimeError("MCP HTTP %s %s" % (e.code, body))
        except urllib.error.URLError as e:
            raise RuntimeError("MCP 网络错误: %s" % e.reason)
        if is_notify:
            return None
        return self._parse(raw)

    @staticmethod
    def _parse(raw):
        raw = raw.strip()
        if raw.startswith("{"):
            try:
                return json.loads(raw)
            except ValueError:
                pass
        for line in raw.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if not chunk:
                    continue
                try:
                    return json.loads(chunk)
                except ValueError:
                    continue
        return None

    def connect(self):
        self._post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcd-secret-kitchen", "version": "1.0.0"},
            },
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, is_notify=True)

    def call(self, name, arguments=None):
        resp = self._post({
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        if not resp:
            return {}
        if "error" in resp:
            return {"_error": resp["error"]}
        return unwrap(resp.get("result", {}))


def unwrap(result):
    """还原 MCP 结果：优先 structuredContent.data。

    麦当劳 MCP 的 content[].text 是给大模型看的 Markdown 说明文档，不是纯 JSON，
    直接 json.loads 会失败；真正的结构化数据在 structuredContent.data 里。
    """
    sc = result.get("structuredContent")
    if isinstance(sc, dict):
        if isinstance(sc.get("data"), (dict, list)):
            return sc["data"]
        return sc
    blocks = result.get("content") or []
    texts = [b.get("text", "") for b in blocks if isinstance(b, dict)]
    joined = "\n".join(t for t in texts if t).strip()
    if not joined:
        return {"_text": ""}
    try:
        parsed = json.loads(joined)
    except ValueError:
        return {"_text": joined}
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), (dict, list)):
        return parsed["data"]
    return parsed


# ------------------------------------------------------------------ 工具函数

def find_rows(obj, prefer=("meals", "products", "items", "list", "records", "data")):
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for k in prefer:
            v = obj.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        for v in obj.values():
            r = find_rows(v)
            if r:
                return r
    return []


def pick(row, *keywords):
    for k, v in row.items():
        kl = str(k).lower()
        if any(w in kl for w in keywords):
            return v
    return None


def as_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------ 演示数据兜底

MOCK_MENU = [
    {
        "name": "板烧鸡腿堡套餐",
        "code": "CMB001",
        "base_price": 26,
        "groups": [
            {"label": "小食", "default": "中薯条",
             "options": [{"name": "中薯条", "delta": 0, "kcal": 320},
                         {"name": "玉米杯", "delta": 0, "kcal": 70},
                         {"name": "薯饼", "delta": 2, "kcal": 150},
                         {"name": "麦辣鸡翅", "delta": 8, "kcal": 240}]},
            {"label": "饮料", "default": "中可乐",
             "options": [{"name": "中可乐", "delta": 0, "kcal": 210},
                         {"name": "无糖可乐", "delta": 0, "kcal": 0},
                         {"name": "拿铁", "delta": 5, "kcal": 120},
                         {"name": "牛奶", "delta": 3, "kcal": 130}]},
        ],
    },
    {
        "name": "巨无霸套餐",
        "code": "CMB002",
        "base_price": 32,
        "groups": [
            {"label": "小食", "default": "中薯条",
             "options": [{"name": "中薯条", "delta": 0, "kcal": 320},
                         {"name": "玉米杯", "delta": 0, "kcal": 70},
                         {"name": "薯饼", "delta": 2, "kcal": 150}]},
            {"label": "饮料", "default": "中可乐",
             "options": [{"name": "中可乐", "delta": 0, "kcal": 210},
                         {"name": "无糖可乐", "delta": 0, "kcal": 0},
                         {"name": "拿铁", "delta": 5, "kcal": 120}]},
            {"label": "加料", "default": "不加",
             "options": [{"name": "不加", "delta": 0, "kcal": 0},
                         {"name": "双层牛肉", "delta": 9, "kcal": 260},
                         {"name": "加芝士片", "delta": 3, "kcal": 90}]},
        ],
    },
]


def mock_menu():
    return MOCK_MENU


# ---------------------------------------------------------------- 组合与命名

def enumerate_combos(combo, limit=400):
    """枚举套餐内可替换选项的所有组合（跳过全默认）。"""
    groups = combo.get("groups") or []
    if not groups:
        return []
    pools = []
    for g in groups:
        opts = g.get("options") or []
        if not opts:
            continue
        pools.append((g.get("label") or "", opts, g.get("default") or ""))

    out = []
    for pick_tuple in product(*[p[1] for p in pools]):
        labels = [p[0] for p in pools]
        defaults = [p[2] for p in pools]
        chosen = list(pick_tuple)
        parts = [o.get("name") for o in chosen]
        if len(labels) == len(defaults) and parts == defaults:
            continue                                   # 原版不上榜
        price = combo.get("base_price", 0) + sum(o.get("delta", 0) for o in chosen)
        kcal = sum(o.get("kcal", 0) for o in chosen)
        out.append({
            "combo": combo.get("name"),
            "code": combo.get("code"),
            "price": price,
            "diff": sum(o.get("delta", 0) for o in chosen),
            "base_known": combo.get("base_known", True),
            "kcal": kcal,
            "parts": parts,
            "swaps": [{"group": labels[i], "from": defaults[i], "to": parts[i]}
                      for i in range(len(parts)) if defaults[i] and defaults[i] != parts[i]],
            "title": "%s · %s" % (combo.get("name"), " + ".join(parts)),
        })
        if len(out) >= limit:
            break
    return out


CODENAMES = [
    (lambda c: c["kcal"] > 0 and c["kcal"] <= 150,
     "轻断食暗号", "热量压到最低，吃完没有负罪感"),
    (lambda c: c["diff"] == 0,
     "白嫖暗号", "一分钱不多花，但换了个搭配"),
    (lambda c: len(c["swaps"]) >= 2,
     "大改暗号", "改得亲妈都不认识"),
    (lambda c: any(k in p for p in c["parts"] for k in ("咖啡", "拿铁", "美式")),
     "提神暗号", "咖啡因到位"),
    (lambda c: any(k in p for p in c["parts"] for k in ("鸡", "牛", "腿", "翅", "肉")),
     "肉食暗号", "蛋白质拉满"),
    (lambda c: any(k in p for p in c["parts"] for k in ("派", "甜筒", "麦旋风", "冰", "芋")),
     "甜品暗号", "糖分优先"),
    (lambda c: len(c["swaps"]) == 1,
     "微调暗号", "只动一处，稳中求变"),
]


def money(c):
    """套餐基准价已知时显示总价，否则退化显示差价。"""
    if c.get("base_known"):
        return "%s 元" % c["price"]
    if c.get("diff", 0) == 0:
        return "不加价"
    return "+%s 元" % c["diff"]


def codename(c):
    for cond, name, desc in CODENAMES:
        try:
            if cond(c):
                return {"name": name, "desc": desc}
        except Exception:
            continue
    return {"name": "自由派暗号", "desc": "你自己开心就好"}


def build_secret_menu(menu, top=12):
    all_combos = []
    for combo in menu:
        all_combos.extend(enumerate_combos(combo))

    seen, uniq = set(), []
    for c in all_combos:
        key = (c["combo"], tuple(c["parts"]))
        if key in seen:
            continue
        seen.add(key)
        c["code_name"] = codename(c)
        uniq.append(c)

    by_price = sorted(uniq, key=lambda c: c["price"])
    by_kcal = sorted(uniq, key=lambda c: c["kcal"])

    # 每个暗号取该类里最典型的一个：先按价格升序，再按热量升序
    buckets = {}
    for c in uniq:
        n = c["code_name"]["name"]
        if n == "自由派暗号":
            continue
        buckets.setdefault(n, []).append(c)
    featured = [sorted(v, key=lambda c: (c["price"], c["kcal"]))[0] for v in buckets.values()]

    return {
        "total": len(uniq),
        "featured": featured[:6],
        "cheapest": by_price[:top],
        "lightest": by_kcal[:top],
        "all": uniq[:top],
    }


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    token = None

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "index.html not found", "text/plain; charset=utf-8")
            return
        if path == "/api/menu":
            self._send(200, json.dumps(run_menu(self.token), ensure_ascii=False))
            return
        self._send(404, json.dumps({"error": "not found"}))


def run_menu(token):
    if not token:
        return {"menu": build_secret_menu(mock_menu()), "_mock": True}
    try:
        c = McpClient(token)
        c.connect()
        menu, err = real_menu(c)
        if err:
            return {"menu": build_secret_menu(mock_menu()),
                    "_mock": True, "_error": err}
        return {"menu": build_secret_menu(menu)}
    except Exception as e:
        return {"menu": build_secret_menu(mock_menu()), "_mock": True, "_error": str(e)}


def parse_toon(text):
    """解析 TOON（Token-Oriented Object Notation）格式。

    麦当劳为降低 Token 消耗，营养信息用 TOON 而非 JSON 数组返回：
        [160]{productName,energyKj,energyKcal,...}:
          猪柳麦满分,null,1288,308,...
          中薯条,null,1210,289,...
    """
    if not isinstance(text, str):
        return []
    lines = text.splitlines()
    fields, out, started = None, [], False
    for line in lines:
        s = line.strip()
        if not started:
            if s.startswith("[") and "{" in s:
                m = re.search(r"\{([^}]*)\}", s)
                if m:
                    fields = [f.strip() for f in m.group(1).split(",")]
                    started = True
            continue
        if not s or s.startswith("["):
            continue
        parts = [p.strip() for p in s.split(",")]
        parts += [""] * (len(fields) - len(parts))
        rec = {}
        for i, f in enumerate(fields):
            v = parts[i]
            rec[f] = None if v in ("null", "NULL", "") else v
        out.append(rec)
    return out


def norm_name(s):
    """归一化餐品名：去掉空格、连字符、括号，便于跨表匹配。"""
    return re.sub(r"[\s\-_()（）·、]", "", str(s))


def build_kcal_table(client):
    """拉取营养表，返回 {归一化餐品名: 千卡}。"""
    resp = client.call("list-nutrition-foods")
    data = resp.get("data") if isinstance(resp, dict) else None
    rows = parse_toon(data) if isinstance(data, str) else find_rows(data)
    table = {}
    for r in rows:
        nm = pick(r, "productname", "name")
        if not nm:
            continue
        kcal = as_int(pick(r, "energykcal", "energyKcal", "kcal"))
        if kcal > 0:
            table[norm_name(nm)] = kcal
    return table


def lookup_kcal(name, table):
    """按名称查热量：精确 → 归一化 → 子串包含，逐级放宽。"""
    if not table or not name:
        return 0
    key = norm_name(name)
    if key in table:
        return table[key]
    for k, v in table.items():
        if k in key or key in k:
            return v
    return 0


def parse_price(s):
    """把 '+ ¥2' / '-1.5' 之类的差价文本解析成数字。"""
    if s is None:
        return 0
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    if not m:
        return 0
    v = float(m.group())
    return int(v) if v == int(v) else v


def parse_detail(detail, kcal_table=None):
    """把 query-meal-detail 的 rounds/choices 解析成组合结构。

    真实返回形如：
      {"code":..., "name":..., "rounds":[{"name":"选择小食","choices":[
          {"name":"麦辣鸡翅2块","isDefault":1,"diffPrice":"+ ¥0"}, ...]}]}
    只有 choices 多于 1 个的 round 才有替换空间。
    """
    if not isinstance(detail, dict):
        return None
    name = pick(detail, "name", "title") or ""
    base = parse_price(pick(detail, "price", "amount", "basePrice"))
    groups = []
    for r in (detail.get("rounds") or []):
        choices = r.get("choices") or []
        if len(choices) < 2:
            continue                                  # 没得换，跳过
        opts, default = [], None
        for ch in choices:
            nm = pick(ch, "name", "title") or ""
            if not nm:
                continue
            opts.append({
                "name": nm,
                "delta": parse_price(ch.get("diffPrice") or ch.get("price")),
                "kcal": as_int(pick(ch, "kcal", "calorie", "energy"))
                       or lookup_kcal(nm, kcal_table or {}),
            })
            if default is None and str(ch.get("isDefault")) in ("1", "true", "True"):
                default = nm
        if not opts:
            continue
        groups.append({
            "label": pick(r, "name") or "选项",
            "default": default or opts[0]["name"],
            "options": opts,
        })
    if not groups:
        return None
    return {"name": name, "code": pick(detail, "code") or "",
            "base_price": base, "base_known": base > 0, "groups": groups}


def real_menu(client, limit=12):
    """自动走通：地址 -> 门店 -> 菜单 -> 套餐详情，全程无需用户输入位置。"""
    addrs = client.call("delivery-query-addresses")
    arows = addrs.get("addresses") if isinstance(addrs, dict) else find_rows(addrs)
    if not arows:
        return None, "账户下没有配送地址"
    aid = pick(arows[0], "addressid", "id")
    if not aid:
        return None, "无法解析 addressId"

    stores = client.call("delivery-query-stores", {"addressId": aid, "beType": 2})
    srows = find_rows(stores)
    if not srows:
        return None, "附近没有可配送门店"
    store_code = pick(srows[0], "storecode")
    be_code = pick(srows[0], "becode")
    if not store_code:
        return None, "无法解析 storeCode"

    kcal_table = build_kcal_table(client)

    cats = client.call("query-meals", {"storeCode": store_code,
                                       "beCode": be_code,
                                       "orderType": 2, "beType": 2})
    catlist = cats if isinstance(cats, list) else find_rows(cats)

    codes, seen = [], set()
    for cat in catlist:
        for m in (cat.get("meals") or []):
            cd = pick(m, "code", "mealcode")
            if cd and cd not in seen:
                seen.add(cd)
                codes.append(cd)

    menu = []
    for cd in codes[:limit]:                          # 控调用次数，避开限流
        d = client.call("query-meal-detail", {"storeCode": store_code,
                                              "beCode": be_code,
                                              "orderType": 2, "beType": 2,
                                              "code": cd})
        parsed = parse_detail(d, kcal_table)
        if parsed:
            menu.append(parsed)
    if not menu:
        return None, "未能解析出任何可替换套餐"
    return menu, None


# -------------------------------------------------------------------- 入口

def cli_report(data):
    m = data["menu"]
    line = "-" * 56
    print(line)
    print("  麦麦暗号厨房 · 隐藏菜单")
    print(line)
    if data.get("_mock"):
        print("  [演示模式] 未检测到 MCD_MCP_TOKEN，以下为示例数据")
    if data.get("_error"):
        print("  [警告] %s" % data["_error"])
    print("  共组合出 %d 种菜单上不存在的搭配\n" % m["total"])

    print("  == 精选暗号 ==")
    for c in m["featured"]:
        print("  [%s] %s" % (c["code_name"]["name"], c["code_name"]["desc"]))
        kcal = "  |  %d kcal" % c["kcal"] if c["kcal"] > 0 else ""
        print("       %s  |  %s%s" % (c["title"], money(c), kcal))
        for s in c["swaps"]:
            print("       换: %s -> %s" % (s["from"], s["to"]))
    print(line)
    print("  == 不加价就能换 ==")
    for c in m["cheapest"][:5]:
        print("    %-42s %s" % (c["title"], money(c)))
    print(line)


def main():
    ap = argparse.ArgumentParser(description="麦麦暗号厨房")
    ap.add_argument("--scan", action="store_true", help="命令行输出隐藏菜单")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--token", default=os.environ.get("MCD_MCP_TOKEN", ""))
    args = ap.parse_args()

    token = args.token.strip()
    if args.scan:
        cli_report(run_menu(token))
        return

    Handler.token = token
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("麦麦暗号厨房已启动: http://127.0.0.1:%d" % args.port)
    print("模式: %s" % ("真实 MCP 数据" if token else "演示模式（未设置 MCD_MCP_TOKEN）"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
