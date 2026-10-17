#!/usr/bin/env python3
"""为一个指标的多个形态寻找"共同的展示窗口"。

为什么需要它：给每个形态单独配图时，如果每张图各用一个窗口，就要反复拿新鲜行情
（前端对行情有最长 5 分钟的缓存），既慢又容易踩缓存。更省事、也更好看的做法是
**所有图共用一个窗口**：窗口里每个形态至少出现一次，且两两隔开足够远（标记的中文
标签不会互相压住）。

本脚本遍历标的池，用滑动窗口找出满足条件的最小窗口，并给出每个形态取用的下标
与窗口的时间边界（后者用于把指标值切到同一段）。

用法：
    python3 find_window.py --token "$TOKEN"
    python3 find_window.py --dev-token --symbols 000001_sz,000002_sz --min-gap 5
    python3 find_window.py --token "$TOKEN" --indicator volume_price --top 5

输出：一段 JSON（symbol / name / window / picks / times / per-form 指数）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.request

DEV_SECRET = "change-this-secret-before-production"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_dev_token(user_id: int = 1, ttl_seconds: int = 900) -> str:
    """本地开发用的短期令牌（依赖 dev 环境的默认 token_secret）。

    仅用于本机联调；生产环境请用正常登录拿到的令牌。
    """
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps({"sub": str(user_id), "exp": int(time.time()) + ttl_seconds},
                              separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    signature = hmac.new(DEV_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


class Api:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, path: str, body: dict | None = None, timeout: int = 120) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}/api/v1{path}",
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Signal-Surface": "private",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def get(self, path: str, timeout: int = 120) -> dict:
        return self._request(path, timeout=timeout)

    def post(self, path: str, body: dict, timeout: int = 180) -> dict:
        return self._request(path, body, timeout=timeout)


def event_fields(api: Api, indicator: str) -> list[str]:
    """从指标元数据里取"事件型"输出作为要观察的形态列表。"""
    indicators = api.get("/indicator/list").get("indicators", [])
    meta = next((item for item in indicators if item.get("type") == indicator), None)
    if meta is None:
        raise SystemExit(f"/indicator/list 里没有指标 {indicator!r}")
    fields = [o["field"] for o in meta.get("outputs", []) if o.get("kind") == "event"]
    if not fields:
        raise SystemExit(f"{indicator} 没有声明 kind=event 的输出，请用 --fields 指定形态字段")
    return fields


def occurrences(values: list[dict], fields: list[str]) -> dict[str, list[int]]:
    return {field: [i for i, row in enumerate(values) if row.get(field) not in (None, 0)] for field in fields}


def pick_separated(lo: int, hi: int, positions: dict[str, list[int]], min_gap: int) -> dict[str, int] | None:
    """在 [lo, hi] 内为每个形态选一个实例，使两两间隔 ≥ min_gap。

    贪心：先处理候选最少的形态，每次选"离已选实例最远"的那个。
    找不到就返回 None（不保证最优，但对"挑一个可见窗口"足够）。
    """
    order = sorted(positions, key=lambda field: len([i for i in positions[field] if lo <= i <= hi]))
    chosen: dict[str, int] = {}
    for field in order:
        candidates = [i for i in positions[field] if lo <= i <= hi]
        if not candidates:
            return None
        best, best_score = candidates[0], -1
        for candidate in candidates:
            score = min((abs(candidate - other) for other in chosen.values()), default=10**6)
            if score > best_score:
                best, best_score = candidate, score
        if chosen and best_score < min_gap:
            return None
        chosen[field] = best
    return chosen


def find_window(positions: dict[str, list[int]], count: int, min_gap: int, max_span: int) -> tuple[int, int, dict[str, int]] | None:
    for span in range(max(3 * min_gap, 30), max_span + 1, 5):
        for lo in range(0, max(1, count - span)):
            picks = pick_separated(lo, lo + span, positions, min_gap)
            if picks:
                return lo, lo + span, picks
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="寻找能同时展示指标所有形态的窗口")
    parser.add_argument("--token", help="Bearer 令牌（生产/联调用）")
    parser.add_argument("--dev-token", action="store_true", help="本地签发短期令牌（依赖 dev 默认密钥）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--indicator", default="volume_price")
    parser.add_argument("--fields", help="逗号分隔的形态字段；默认取指标的 kind=event 输出")
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--kline-limit", type=int, default=500)
    parser.add_argument("--market", default="stock")
    parser.add_argument("--symbol-limit", type=int, default=20, help="从标的池取前多少个标的")
    parser.add_argument("--symbols", help="逗号分隔的指定标的；给了就不查标的池")
    parser.add_argument("--min-gap", type=int, default=5, help="窗口内两两实例的最小间隔（根）")
    parser.add_argument("--max-span", type=int, default=150, help="窗口最大跨度（根）")
    parser.add_argument("--top", type=int, default=3, help="输出前几个候选")
    args = parser.parse_args()

    if not args.token and not args.dev_token:
        parser.error("需要 --token 或 --dev-token")
    api = Api(args.base_url, args.token or mint_dev_token())

    fields = [item.strip() for item in args.fields.split(",")] if args.fields else event_fields(api, args.indicator)

    if args.symbols:
        symbols = [{"symbol": item.strip()} for item in args.symbols.split(",") if item.strip()]
    else:
        symbols = api.get(f"/symbols?market={args.market}&limit={args.symbol_limit}").get("symbols", [])

    candidates = []
    for item in symbols:
        symbol = item["symbol"]
        try:
            response = api.post("/indicator/calculate", {
                "symbol": symbol,
                "timeframe": args.timeframe,
                "kline_limit": args.kline_limit,
                "indicators": [{"type": args.indicator, "params": {}}],
            })
        except Exception as error:  # 单个标的失败不影响整体
            print(f"skip {symbol}: {error}", file=sys.stderr)
            continue
        if response.get("errors"):
            continue
        values = response["results"][0]["values"]
        positions = occurrences(values, fields)
        missing = [field for field, hits in positions.items() if not hits]
        if missing:
            continue
        found = find_window(positions, len(values), args.min_gap, args.max_span)
        if not found:
            continue
        lo, hi, picks = found
        candidates.append({"symbol": symbol, "name": item.get("name"), "window": [lo, hi], "span": hi - lo, "picks": picks})

    if not candidates:
        raise SystemExit("没有找到同时包含所有形态的窗口；可尝试放宽 --min-gap / --max-span 或换标的池")

    candidates.sort(key=lambda item: item["span"])
    best = candidates[0]
    # 补上窗口的时间边界（切片指标值要用）
    klines = api.get(f"/klines/{best['symbol']}?timeframe={args.timeframe}&limit={args.kline_limit}").get("data", [])
    lo, hi = best["window"]
    best["times"] = {"from": klines[lo]["open_time"], "to": klines[hi - 1]["open_time"]}
    best["fields"] = fields

    output = {"best": best, "runners_up": candidates[1:args.top]}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
