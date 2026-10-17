# 配图流水线（真实图表）

目标：为指标的所有形态各出一张图 + 一张总览（量价指标是 8 个形态），**全部从真实运行的图表导出**，尺寸一致、配色与线上一致。
所有代码都经过实跑验证；括号里写的是"为什么"，照着做能避开已经踩过的坑。

## 前置

- 后端 `127.0.0.1:8000`、前端私有站 `127.0.0.1:4175` 都在运行（`./start-private.sh`）。
- 浏览器用 in-app browser（Browser Use 的 `cua` 坐标点击）。
  **Playwright 的 `locator.click()` 在本应用上经常超时**，用 `getBoundingClientRect()` 算出中心坐标再 `tab.cua.click({x,y})`。
- 临时令牌（**仅本地开发**）：前端令牌存在 localStorage 的 `signal_layer_access_token`，
  dev 环境 `token_secret` 是默认值，可以本地签发一个短期 JWT 塞进去：

```js
const crypto = await import("node:crypto");
const b64 = (d) => Buffer.from(d).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const si = `${b64(JSON.stringify({ alg: "HS256", typ: "JWT" }))}.${b64(JSON.stringify({ sub: "1", exp: Math.floor(Date.now() / 1000) + 7200 }))}`;
const token = `${si}.${b64(crypto.createHmac("sha256", "change-this-secret-before-production").update(si).digest())}`;
await tab.playwright.evaluate(([t]) => localStorage.setItem("signal_layer_access_token", t), [token]);
await tab.reload();
```

收尾时必须 `localStorage.removeItem(...)` 并 reload，回到登录页。

## 第 1 步：先找一个能同时容纳所有形态的窗口

用 `scripts/find_window.py`（见同目录），它会遍历标的池，找出"每个形态至少一次、两两间隔 ≥ N 根"的最小窗口：

```bash
cd /Users/rui/workspace/trade
python3 .agents/skills/indicator-article/scripts/find_window.py \
  --token "$TOKEN" --timeframe 1d --kline-limit 500 --min-gap 5 --max-span 150
```

输出（示例）：

```json
{"symbol": "000006_sz", "name": "深振业Ａ", "window": [213, 278],
 "picks": {"climax_top": 218, "price_volume_rise": 235, "...": 0}}
```

为什么要先扫：**一张总览图 + 七张单形态图共用同一个窗口**，这样只需要一次新鲜行情请求
（见第 4 步的缓存说明），而且标记之间不会互相压住。

再取这个窗口的时间范围（切片指标值要用）：

```bash
python3 - <<'PY'
# GET /api/v1/klines/<symbol>?timeframe=1d&limit=500 -> data
# from = rows[start].open_time   to = rows[end-1].open_time
PY
```

## 第 2 步：浏览器里搭好现场

1. 选市场「股票」→ 在标的搜索框里 `fill("000006")` → 点候选行（`cua.click`）。
2. 点「+ 添加指标」→ 点要讲的指标名。
3. **关掉「缠论」图层。** 它是独立的一条 500 根长度序列，会把时间轴硬撑回 500 根，
   导致 K 线只占宽度的一小条。

## 第 3 步：装 fetch 钩子（窗口切片 + 注入标记）

```js
window.__vpPristineFetch = window.fetch;
window.__vpWindow = { start: 210, end: 286 };                  // 下标区间（左闭右开）
window.__vpWinTimes = { from: 1753891200000, to: 1763654400000 }; // 同一窗口的时间边界（毫秒）
window.__vpMarkers = [];                                        // 当前要注入的 marker 规格
window.fetch = async (...args) => {
  const url = typeof args[0] === "string" ? args[0] : (args[0] && args[0].url) || "";
  const resp = await window.__vpPristineFetch(...args);
  if (url.includes("/klines")) {
    const data = await resp.clone().json();
    const rows = data.data || [];
    const slice = rows.slice(Math.max(0, window.__vpWindow.start),
                             Math.min(rows.length, window.__vpWindow.end));
    return new Response(JSON.stringify({ ...data, data: slice }),
      { status: resp.status, headers: { "Content-Type": "application/json" } });
  }
  if (!url.includes("/indicator/calculate")) return resp;
  const data = await resp.clone().json();
  const t = window.__vpWinTimes;
  for (const r of data.results || []) {
    if (r.type !== "<indicator_type>") continue;
    r.values = r.values.filter((v) => v.time >= t.from && v.time <= t.to);  // 必须切！
    r.render.markers = window.__vpMarkers;
  }
  return new Response(JSON.stringify(data),
    { status: resp.status, headers: { "Content-Type": "application/json" } });
};
```

**行情和指标值必须一起切。** 只切行情的话，Lightweight-Charts 的时间轴取所有序列的并集，
K 线会被挤到宽度的一小段里。

## 第 4 步：注入标记并抓图

每张图只注入目标形态，**直接用线上配色**（买入侧 `#ff5b62`、卖出侧 `#2fc58d`）。
前端按字段值的正负决定用 `buy_color` 还是 `sell_color`，所以把两个颜色都设成该侧颜色最省事：

```js
window.__vpMarkers = [{
  field: "volume_breakout", buy_color: "#ff5b62", sell_color: "#ff5b62",
  buy_label: "放量突破", sell_label: "放量突破",
  price_field: null, label_index_field: null, label_tag_field: null,
  label_tags: {}, size: 1, spacing: 0,
}];
```

> 总览图注入全部形态时，**每个形态各用自己那一侧的颜色**。所有形态共用一个颜色，
> 会把卖出侧的箭头也染成红色（这是实际犯过的错）。

改完 `__vpMarkers` 后**触发一次重算**：依次点「60分钟」→「日线」。
（指标计算的响应不会被 `requestCache` 缓存，但应用可能因为状态没变而不重发；换周期能保证重发一次。）

抓图：把每个 pane 行和底部时间轴的 canvas **按各自在容器内的真实偏移**画到一张离屏 canvas 上，
再缩放到固定输出尺寸：

```js
const root = document.querySelector(".tv-lightweight-charts");
const rootRect = root.getBoundingClientRect();
const rows = [...root.querySelector("table").querySelectorAll(":scope > tr")];
const paneRows = rows.filter((r) => r.querySelector("canvas") && r.getBoundingClientRect().height > 20);
const vis = paneRows.flatMap((r) => [...r.querySelectorAll("canvas")])
                    .filter((c) => c.width > 0 && c.height > 0);
const dpr = vis[0].width / vis[0].getBoundingClientRect().width;

const off = document.createElement("canvas");
off.width = Math.round(rootRect.width * dpr);
off.height = Math.round(rootRect.height * dpr);
const ctx = off.getContext("2d", { willReadFrequently: true });
for (const c of vis) {
  const cr = c.getBoundingClientRect();
  ctx.drawImage(c, Math.round((cr.left - rootRect.left) * dpr),
                   Math.round((cr.top - rootRect.top) * dpr));
}

const norm = document.createElement("canvas");
norm.width = 1242; norm.height = 882;              // 输出尺寸写死
norm.getContext("2d").drawImage(off, 0, 0, 1242, 882);
const url = norm.toDataURL("image/png");           // 在 Node 侧写文件
```

两个必须遵守的点：

- **按偏移绘制。** 全部 `drawImage(c, 0, 0)` 会把价格轴叠到图表左边。
- **输出尺寸写死。** 同一次会话里 devicePixelRatio 会在 1x / 2x 之间跳，
  按 dpr 缩放会让同一批图大小不一。

## 第 5 步：验证（看不到图时的唯一办法）

模型**看不到图片**，所以只能程序化核对。**不要用"注入 / 不注入"两次截图的像素差**——
两次抓图未像素对齐（dpr 会变），差值会被对齐噪声淹没（实测约 10 万像素）。

用**颜色占比**，它不受整体缩放影响：

```js
// 分别抓三次：不注入 / 只注入买入侧形态 / 只注入卖出侧形态
const greenShare = green / (green + red);   // 统计 ±24 容差内的 #2fc58d 与 #ff5b62
```

判据：只注入买入侧时 `greenShare` 应**下降**；只注入卖出侧时应**上升**。
（实测基准 0.4824 → 买入侧 0.4634 ↓ / 卖出侧 0.4890 ↑。）

另外核对：输出尺寸一致、文件大小不是空图、PNG 头正确、图注编号连续。

## 第 6 步：还原现场

```js
window.fetch = window.__vpPristineFetch;
delete window.__vpPristineFetch;   // 以及 __vpWindow/__vpWinTimes/__vpMarkers 等临时全局
localStorage.removeItem("signal_layer_access_token");
```

然后 reload（回到登录页）、把视口还原成 1280×720、关掉自己新建的标签页。
最后确认用户数据没被改：`GET /api/v1/templates` 的数量应与开工前一致。

## 缓存速查（最大的时间陷阱）

`signal-layer/src/api/requestCache.ts`：

| 周期 | TTL |
| --- | --- |
| 5m | 15 秒 |
| 15m | 30 秒 |
| 30m | 60 秒 |
| 60m | 90 秒 |
| 1d | **300 秒** |

- key 是 `kline:${symbol}:${timeframe}:${limit}`（`api/kline.ts`）；`chan` 同样走这个缓存。
- **命中缓存的响应不会经过 fetch 钩子**，所以窗口切片对它无效。
- 结论：**一个新窗口 = 一个新标的**（冷 key）；同一标的内 5 分钟不要改窗口参数。
- 若确实需要同标的换窗口，等 TTL 过期，或换一个从未请求过的周期（但周期一变口径语境也变，不推荐）。
