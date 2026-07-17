# adshare 实时数据 tushare 扩展设计

> 版本: 0.2.0-draft  
> 更新日期: 2026-07-17  
> 状态: 设计评审通过（修订后待开发）  
> 修订记录: v0.2 按评审意见修正——文件路径对齐 monorepo 结构、key 契约落到
> `realtime_keys.py`、REST 路径统一为 rt_k/rt_min、配置落位到拆分后的 env 文件、
> 风险表补充订阅规模与 snapshot 基线负载

---

## 0. 背景与动机

adshare 当前已具备实时行情链路：

- **Worker 进程**（`amazingdata/realtime.py`）通过 AmazingData SDK 的 `SubscribeData` 订阅 Level-1 快照、指数快照、实时 K 线（默认 `min1`）
- 数据落地到 **Redis**（key + Pub/Sub）
- **API 进程** 通过 `/realtime/*` REST + `/realtime/ws` WebSocket + `/realtime/sse` 暴露给下游

但对使用 Tushare 协议的客户端（量化脚本、AI Agent）而言，缺少对应的 `tushare` 协议入口：

| Tushare 端点 | 中文名 | 当前 adshare 状态 |
|---|---|---|
| [`rt_k`](https://tushare.pro/document/2?doc_id=372) | 实时行情（盘口快照） | ❌ 仅 `/realtime/quote/{code}` 内部接口 |
| [`rt_min`](https://tushare.pro/document/2?doc_id=374) | 实时分钟 K 线 | ❌ 仅 `/realtime/kline/{code}?period=min1` 内部接口 |

本设计在 **不引入新数据源、不改 SDK 调用方式** 的前提下，把已有 Redis 实时数据按 tushare Pro 协议重新封装，并扩展多周期 K 线订阅能力。

---

## 1. 设计目标

1. **覆盖范围**：A 股 SH+SZ 全市场（SDK `EXTRA_STOCK_A_SH_SZ` 口径，当前实际约 5,200 只）。
2. **端点对等**：
   - `POST /tushare` 统一入口支持 `api_name=rt_k`
   - `POST /tushare` 统一入口支持 `api_name=rt_min`
3. **REST 直连**：同步暴露 `GET/POST /tushare/realtime/rt_k` 与 `GET/POST /tushare/realtime/rt_min`
4. **多周期**：1MIN / 5MIN / 15MIN / 30MIN / 60MIN 全部支持
5. **响应字段**：符合 tushare Pro 字段命名习惯
6. **零成本复用**：完全基于现有 Redis 实时数据 + 现有订阅通道，不引入新 SDK 调用
7. **降级兼容**：与现有 `/realtime/*` REST/WebSocket/SSE 通道并存，互不干扰

---

## 2. 端点语义澄清

### 2.1 [`rt_k`](https://tushare.pro/document/2?doc_id=372)

- **含义**：单只股票的 Level-1 快照行情
- **频率**：约每 3 秒推送一次（交易所推送节拍）
- **字段**：买卖 5 档盘口、最新价、成交量、成交额、涨停价、跌停价等
- **数据源映射**：AmazingData SDK `onSnapshot` + `Period.snapshot.value`
- **当前 adshare 路径**：snapshot key（`realtime_keys.REALTIME_QUOTE_KEY` 契约，见 §6.1）+ `adshare:realtime:quote` 频道

### 2.2 [`rt_min`](https://tushare.pro/document/2?doc_id=374)

- **含义**：实时分钟 K 线（最近若干根）
- **频率**：min1 每分钟推送一次；min5 每 5 分钟一次；以此类推
- **字段**：`ts_code / freq / trade_time / open / high / low / close / volume / amount`
- **数据源映射**：AmazingData SDK `OnKLine` + adapter `_KLINE_PERIOD_MAP`（已支持 min1/3/5/10/15/30/60/120，本设计取 1/5/15/30/60）
- **当前 adshare 路径**：K 线 key（`realtime_keys.REALTIME_KLINE_KEY` 契约）+ `adshare:realtime:kline:{period}` 频道

---

## 3. 现状能力盘点

### 3.1 Worker 侧

文件：`amazingdata/realtime.py`（`RealtimePublisher` 类，monorepo 拆分后 realtime 与 publisher 已合并为单文件入口）

- 已注册三类回调：
  - `on_snapshot` → snapshot key + `adshare:realtime:quote` 频道
  - `on_index_snapshot` → index key + `adshare:realtime:index` 频道
  - `on_kline`（按 `Settings.realtime_kline_periods`）→ K 线 key + `adshare:realtime:kline:{period}` 频道
- 代码表来源：**直接从 SDK `get_code_list("EXTRA_STOCK_A_SH_SZ")` 拉取**（约 5,200 只，每日最新代码表）；`meta/codes.parquet` 仅作兜底，不再依赖
- 账号模型：realtime / batch 使用**各自独立的 TGW 账号**（`realtime.env` / `batch.env`），无单连接争用
- 写入模型：**单 key 覆盖**（`set_realtime_market` → `SETEX`），不累积历史
- 序列化：`_serialize_data` 对 SDK 对象做全字段 dump（`dir()` 扫描），SDK snapshot 自带的 `bid_price1..5` / `bid_volume1..5` / `ask_price1..5` / `ask_volume1..5` 已在 Redis payload 中，**rt_k 映射无需改 worker 序列化**

### 3.2 API 侧

文件：
- `adshare/routers/realtime.py` — REST + WS + SSE
- `adshare/services/realtime_broadcast.py` — Redis Pub/Sub 监听 → WS/SSE 广播
- `adshare/core/cache.py` — `get_realtime_market` / `set_realtime_market` / `_make_key`
- `adshare/core/realtime_keys.py` — key/channel 契约（两进程共用的事实标准）

- `RealtimeBroadcastService.start()` 已经按 `realtime_kline_periods` 动态拼频道，扩展无需改
- WS/SSE payload 已是 dict 直传，新增 `type=tick/kline` 无需改广播逻辑

### 3.3 已有 tushare 路由骨架

文件：`adshare/routers/tushare/`

- `__init__.py`：统一入口 `POST /tushare`，通过 `stock.HANDLERS or index.HANDLERS` 分发；
  路由级依赖 `tushare_auth`（支持 body token / `X-API-Key` header / `api_key` query 三种认证方式，新端点自动继承）
- `stock.py`：注册了 `daily / weekly / monthly / stock_basic / trade_cal / adj_factor / suspend_d / limit_list`
- `index.py`：仅占位
- `common.py`：提供 `df_to_tushare_payload` / `filter_fields` / 参数解析
- **注意**：统一入口以 `handler(params, fields, service=..., up_service=..., down_service=...)` 签名调用，
  rt_k/rt_min handler 忽略这些 kwargs、内部经 `get_cache_manager()` 取 Redis 即可（见 §7.4）

---

## 4. 数据规模测算

### 4.1 假设

- 标的数：A 股 SH+SZ 当前实际约 5,200 只；**容量按 5,400 只上限测算**（留余量）
- 交易时段：4 小时 / 日 = 240 分钟
- 每根 K 线 payload：JSON ≈ 220 bytes
- Redis Stream 内部开销：≈ +10%

### 4.2 单日盘中累积（历史留痕版）

将每次推送累积到 Redis Stream 而非覆盖：

| 周期 | 单只根数/日 | 总条数 | 内存（含 10% 开销）|
|---|---|---|---|
| min1 | 240 | 1,296,000 | ~313 MB |
| min5 | 48 | 259,200 | ~63 MB |
| min15 | 16 | 86,400 | ~21 MB |
| min30 | 8 | 43,200 | ~10 MB |
| min60 | 4 | 21,600 | ~5 MB |
| **合计** | **316** | **1,706,400** | **~412 MB** |

### 4.3 单 key 覆盖版（无历史留痕）

每只股票每周期只存最新 1 根：

| 周期 | keys | 单条大小 | 内存 |
|---|---|---|---|
| min1 | 5,400 | 220 B | ~1.2 MB |
| min5 | 5,400 | 220 B | ~1.2 MB |
| min15 | 5,400 | 220 B | ~1.2 MB |
| min30 | 5,400 | 220 B | ~1.2 MB |
| min60 | 5,400 | 220 B | ~1.2 MB |
| **合计** | **27,000** | | **~6 MB** |

### 4.4 写入压力

- K 线新增部分：
  - min1：5,400 推送/分钟 ≈ **90 push/s**（平均），峰值 200+ push/s
  - min5：18 push/s；min15：6 push/s；min30：3 push/s；min60：1.5 push/s
  - **K 线合计 ~120 ops/s**（XADD；若每次跟 EXPIRE 则 ~240 ops/s，量级仍可忽略）
- snapshot 基线负载（**现状已存在，非本设计新增**）：
  - 5,200 只每 ~3 秒一条 ≈ 1,700 push/s，每条 SETEX + PUBLISH 两条命令 ≈ **3,500 ops/s**
- 合计峰值约 4,000 ops/s，远低于 Redis 单机能力（10 万 ops/s）

### 4.5 决策

| 维度 | 单 key 覆盖 | Stream 累积 |
|---|---|---|
| 内存 | ~6 MB | ~412 MB |
| 历史可查 | ❌ 仅最新 1 根 | ✅ 最近 N 根 |
| tushare `rt_min` 返回 | 仅当前 | 最近 N 根（典型 240 根） |
| TTL | 5 分钟（自动过期） | 1 天（盘后清零） |
| **推荐** | 仅 snapshot 用 | ✅ **K 线用** |

---

## 5. 架构概览

```
┌──────────────────────────────────────────────────────────────────┐
│                       Tushare Client                              │
│              pro.rt_min(...)  /  pro.rt_k(...)                    │
└──────────────────────────────────────────────────────────────────┘
                                │  POST /tushare
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                    FastAPI Tushare Router                         │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ _resolve_handler(api_name)                                  │  │
│  │   ├─ stock.HANDLERS                                         │  │
│  │   ├─ index.HANDLERS                                         │  │
│  │   └─ realtime.HANDLERS  ← 新增                              │  │
│  │       ├─ rt_k   → handle_rt_k()                             │  │
│  │       └─ rt_min → handle_rt_min()                           │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                  Realtime Tushare Mapper                          │
│        quote_to_tushare_row() / kline_to_tushare_row()            │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                     CacheManager                                  │
│   get_realtime_market(REALTIME_QUOTE_KEY, code)                   │
│   XREVRANGE <kline-hist stream key（realtime_keys 契约）>          │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                          Redis                                    │
│   Key:    snapshot（REALTIME_QUOTE_KEY 契约，现状）                │
│   Stream: K 线历史（REALTIME_KLINE_HIST_KEY 契约，新增，见 §6.2）  │
└──────────────────────────────────────────────────────────────────┘
                                ▲
                                │  XADD / SETEX
                                │
┌──────────────────────────────────────────────────────────────────┐
│              amazingdata/realtime.py (SubscribeData)              │
│   on_snapshot → REALTIME_QUOTE_KEY                                │
│   on_kline    → K 线 Stream（新增）+ 原 SETEX（保留）              │
└──────────────────────────────────────────────────────────────────┘
                                ▲
                                │
┌──────────────────────────────────────────────────────────────────┐
│                     AmazingData SDK                               │
│             onSnapshot / OnKLine (Period.min1..day)               │
└──────────────────────────────────────────────────────────────────┘
```

---

## 6. 数据结构

### 6.0 Key 契约（重要）

所有 realtime key / channel 的唯一事实标准是 `adshare/core/realtime_keys.py` +
`CacheManager._make_key()`，worker 写入与 API 读取**必须都经由该助手生成 key，
禁止在代码里 f-string 硬编码前缀**。

现有 key 的实际形态（注意双层 "realtime" 是 `_make_key` 拼接规则造成的现状瑕疵，
本次不做破坏性修复，新 key 沿用同一助手以保持一致）：

```
adshare:realtime:realtime:quote:600519.SH        ← snapshot（_make_key("realtime", REALTIME_QUOTE_KEY, code)）
adshare:realtime:realtime:kline:min1:600519.SH   ← K 线单 key（现状）
```

### 6.1 复用：snapshot（保持现状）

- Redis Key：`_make_key("realtime", REALTIME_QUOTE_KEY, code)`（契约 `REALTIME_QUOTE_KEY = "realtime:quote"`）
- 写入方：`RealtimePublisher._handle_snapshot`（无改动）
- 读取方：`handle_rt_k` 新增（经 `get_realtime_market(REALTIME_QUOTE_KEY, code)`）

### 6.2 新增：K 线 Stream

每只股票每个频率一个 Redis Stream：

- 契约常量（新增到 `realtime_keys.py`）：`REALTIME_KLINE_HIST_KEY = "realtime:kline:hist"`
- Key 生成：`cache._make_key("realtime", f"{REALTIME_KLINE_HIST_KEY}:{freq}", code)`
- 写入：`XADD key * trade_time {ms} data {json}`
- 读取：`XREVRANGE key + - COUNT N`（拉最近 N 根）
- TTL：`EXPIRE key 86400`，盘后自然过期（每次 XADD 后设置即可，~120 ops/s 量级可忽略）
- 上限：`XADD MAXLEN ~ 240`（min1 单只单日根数）

#### 与现有 SETEX 的兼容性

现有 `_handle_kline` 走 `set_realtime_market` → `SETEX`，下游 `/realtime/kline/{code}` 直接读单 key。

为避免破坏现有调用方，**保留单 key 行为作为 `current`**，同时新增 `XADD` 累积。两种存储并行：

```
单 key:  _make_key("realtime", "realtime:kline:{freq}", code)        (SETEX, 最新 1 根，5 分钟 TTL)
Stream:  _make_key("realtime", "realtime:kline:hist:{freq}", code)   (STREAM, 历史累积，1 天 TTL)
```

下游 `/realtime/kline/{code}` 走 SETEX 不变；tushare `rt_min` 走 Stream。

### 6.3 Pub/Sub 频道（不变）

- `adshare:realtime:quote`（已存在）
- `adshare:realtime:kline:{freq}`（已存在）

`RealtimeBroadcastService.start()` 已经动态拼所有 `realtime_kline_periods` 频道，扩展 freq 列表无需改代码。

---

## 7. 模块设计

### 7.1 新增/修改文件

```
adshare/
├── core/
│   └── realtime_keys.py             # 修改：新增 REALTIME_KLINE_HIST_KEY 契约常量
├── routers/
│   └── tushare/
│       └── realtime.py              # 新增：rt_k / rt_min handler + REST 路由
└── services/
    └── realtime_tushare_mapper.py   # 新增：snapshot / kline → tushare 字段映射
amazingdata/
└── realtime.py                      # 修改：_handle_kline 增加 XADD Stream（保留 SETEX）
```

### 7.2 配置变更

均加在**共享 `Settings`**（`adshare/core/config.py`）；worker 进程经
`WorkerSettings.__getattr__` 自动转发读取，`amazingdata/config.py` 无需改动：

```python
# 已有（默认值调整）
realtime_kline_periods: List[str] = Field(
    default=["min1", "min5", "min15", "min30", "min60"],
    alias="REALTIME_KLINE_PERIODS",
)

# 新增
realtime_kline_history_ttl: int = Field(
    default=86400,
    alias="REALTIME_KLINE_HISTORY_TTL",
    description="K 线 Stream 在 Redis 中的 TTL（秒），盘后自然过期",
)
realtime_kline_max_bars: int = Field(
    default=240,
    alias="REALTIME_KLINE_MAX_BARS",
    description="单只股票单个 freq Stream 最多保留的根数（XADD MAXLEN）",
)
```

**env 落位**（配置已按进程拆分，两边都要写）：

- `amazingdata/realtime.env`（worker 容器读取）：`REALTIME_KLINE_PERIODS` / `REALTIME_KLINE_HISTORY_TTL` / `REALTIME_KLINE_MAX_BARS`
- `adshare/.env`（API 进程读取）：`REALTIME_KLINE_HISTORY_TTL` / `REALTIME_KLINE_MAX_BARS`（API 端分页默认值用）

### 7.3 worker 端改动

`amazingdata/realtime.py` 的 `_handle_kline`：在原 `set_realtime_market`（SETEX）之外新增：

```python
from adshare.core.realtime_keys import REALTIME_KLINE_HIST_KEY

stream_key = cache._make_key(
    "realtime", f"{REALTIME_KLINE_HIST_KEY}:{period_str}", code,
)
cache.redis.xadd(
    stream_key,
    {"trade_time": ms, "data": json.dumps(serialized)},
    maxlen=settings.realtime_kline_max_bars,
    approximate=True,
)
cache.redis.expire(stream_key, settings.realtime_kline_history_ttl)
```

> 注：单 key SETEX 路径保留，下游 `/realtime/kline/{code}` 行为不变。

### 7.4 API 端 tushare handler

`adshare/routers/tushare/realtime.py`（新增）：

```python
HANDLERS = {
    "rt_k": handle_rt_k,
    "rt_min": handle_rt_min,
}
```

**handler 签名适配**：统一入口以 `handler(params, fields, service=..., up_service=...,
down_service=...)` 调用。rt_k/rt_min handler 声明同样的 kwargs 并忽略，
内部经 `get_cache_manager()` 取 Redis。

#### handle_rt_k

- 入参：`ts_code`（必填，单只股票代码）
- 出参：单行 tushare payload
- 字段映射（`services/realtime_tushare_mapper.py`）：

| Redis 字段 | tushare 字段 |
|---|---|
| `code` | `ts_code` |
| `last` | `price` |
| `open/high/low` | `open/high/low` |
| `pre_close` | `pre_close` |
| `volume` | `vol` |
| `amount` | `amount` |
| `num_trades` | `num_trades` |
| `bid_price1..5` / `ask_price1..5` | `b1_p` / `a1_p` 等（tushare 命名）|
| `bid_volume1..5` / `ask_volume1..5` | `b1_v` / `a1_v` 等 |
| `high_limited/low_limited` | `high_limit/low_limit` |
| `trade_time` | `trade_time`（datetime）|

> 实施时先在盘中 dump 一条真实 snapshot payload 确认字段名
> （SDK 手册中卷名字段排版为 `ask _volume1` 带空格，疑似文档瑕疵，
> 以实际 payload 为准）。

#### handle_rt_min

- 入参：`ts_code`（必填）、`freq`（必填，1/5/15/30/60 MIN）、可选 `start_time` / `end_time` / `limit`
- freq 映射：tushare `"1MIN"` → `"min1"`、`"5MIN"` → `"min5"` ...
- 出参：最多 N 行 K 线，按 `trade_time` 升序
- 数据源：`XREVRANGE <stream key> + - COUNT {limit}`，再反转为升序
- 字段映射：

| Redis Stream payload | tushare 字段 |
|---|---|
| `ts_code` | `ts_code` |
| `trade_time` (ms) | `trade_time` |
| `freq` | `freq` |
| `open/high/low/close` | `open/high/low/close` |
| `volume` | `vol` |
| `amount` | `amount` |

#### REST 直连路由

挂在 tushare 路由下（最终路径带 `/tushare` 前缀），命名与 tushare 端点保持一致，
避免与主 realtime 路由器的 `/realtime/quote/{code}` 混淆：

```python
@router.post("/realtime/rt_k")
@router.get("/realtime/rt_k")
async def tushare_rt_k(...): ...      # → /tushare/realtime/rt_k

@router.post("/realtime/rt_min")
@router.get("/realtime/rt_min")
async def tushare_rt_min(...): ...    # → /tushare/realtime/rt_min
```

### 7.5 字段映射模块

`adshare/services/realtime_tushare_mapper.py`：

```python
def quote_to_tushare_row(code: str, payload: dict) -> dict:
    """snapshot dict → tushare rt_k 行"""
    ...

def kline_to_tushare_row(code: str, freq: str, payload: dict) -> dict:
    """K 线 dict → tushare rt_min 行"""
    ...

def quote_columns() -> list[str]:
    """tushare rt_k 输出字段顺序"""
    ...

def kline_columns() -> list[str]:
    """tushare rt_min 输出字段顺序"""
    ...
```

复用 `routers/tushare/common.py:df_to_tushare_payload`。

---

## 8. 配置示例

`amazingdata/realtime.env`（worker）：

```bash
# 5 周期全开（原默认只有 min1）
REALTIME_KLINE_PERIODS=["min1","min5","min15","min30","min60"]

# K 线历史 TTL：1 天（盘后清零）
REALTIME_KLINE_HISTORY_TTL=86400

# 单只股票单个 freq Stream 最大保留 240 根（min1 全天根数）
REALTIME_KLINE_MAX_BARS=240
```

`adshare/.env`（API，分页/TTL 默认值同样需要）：

```bash
REALTIME_KLINE_HISTORY_TTL=86400
REALTIME_KLINE_MAX_BARS=240
```

---

## 9. 接口响应示例

### 9.1 rt_k

请求：
```json
{
  "api_name": "rt_k",
  "token": "...",
  "params": {"ts_code": "600000.SH"},
  "fields": ""
}
```

响应：
```json
{
  "code": 0,
  "msg": "",
  "data": {
    "fields": [
      "ts_code", "trade_time", "price", "open", "high", "low",
      "pre_close", "vol", "amount", "num_trades",
      "b1_p", "a1_p", "b1_v", "a1_v",
      "high_limit", "low_limit"
    ],
    "items": [
      ["600000.SH", "2026-07-16 14:32:15", 10.45, 10.32, 10.50, 10.28,
       10.30, 52345678, 547832156.32, 12345,
       10.44, 10.45, 1000, 500,
       11.33, 9.27]
    ]
  }
}
```

### 9.2 rt_min

请求：
```json
{
  "api_name": "rt_min",
  "token": "...",
  "params": {"ts_code": "600000.SH", "freq": "1MIN"},
  "fields": ""
}
```

响应：
```json
{
  "code": 0,
  "msg": "",
  "data": {
    "fields": ["ts_code", "trade_time", "freq", "open", "high", "low", "close", "vol", "amount"],
    "items": [
      ["600000.SH", "2026-07-16 14:30:00", "1MIN", 10.42, 10.43, 10.41, 10.43, 12345, 12876543.0],
      ["600000.SH", "2026-07-16 14:31:00", "1MIN", 10.43, 10.45, 10.43, 10.44, 18765, 19584321.0],
      ["600000.SH", "2026-07-16 14:32:00", "1MIN", 10.44, 10.46, 10.44, 10.45, 15432, 16123456.0]
    ]
  }
}
```

---

## 10. 降级与边界

| 场景 | 行为 |
|---|---|
| Redis key 不存在（无订阅数据） | `rt_k` 返回 `tushare_empty()`（空 data） |
| `ts_code` 不在订阅列表 | 同上，提示用户该股票无实时数据 |
| `freq` 不在订阅周期列表 | 报错 `InvalidParameterError`，提示合法 freq |
| Stream 为空（盘前 / 已收盘清空） | `rt_min` 返回空 data |
| `trade_time` 转换失败 | 字段置 null，不抛错 |
| Worker 进程未启动 / SDK 离线 | API 端只读 Redis，全部端点降级为空 data |

---

## 11. 测试

### 11.1 单元测试

- `tests/services/test_realtime_tushare_mapper.py`
  - `quote_to_tushare_row` 字段映射、空 dict、字段缺失
  - `kline_to_tushare_row` 字段映射、freq 大小写、`trade_time` 格式

### 11.2 集成测试

- `tests/routers/tushare/test_realtime.py`
  - 直接调 `handle_rt_k`，注入 mocked CacheManager
  - 直接调 `handle_rt_min`，注入 mocked Redis stream
  - `POST /tushare` 统一入口分发（`api_name=rt_k` / `rt_min`）

### 11.3 端到端

- 启动 worker + API
- 通过 ws://.../realtime/ws 订阅触发 Redis 写入
- 通过 POST /tushare 验证读取

---

## 12. 风险与权衡

| 风险 | 缓解 |
|---|---|
| **订阅规模未验证**：5,200 只 ×（snapshot + 5 个 K 线周期）≈ 3.2 万订阅。TGW 登录返回 `SubscribeLimitNum=0`（疑似不限），但未实测 | **灰度上线**：先 500 只 × 5 周期观察推送延迟 / 丢包 / 带宽（账户 PushBandwidth=2048），稳定后扩到全量 |
| 多周期订阅推高 TGW 推送带宽占用 | 灰度期间监控 SDK 侧错误日志（OnClose / socket timeout），必要时砍到 min1+min5 两周期 |
| `_SDK_CALL_LOCK` 在 K 线密集推送时阻塞 | K 线回调只做 XADD + SETEX，不调用 SDK，锁影响小 |
| Redis 内存占用 ~412 MB（Stream 版）| `MAXLEN ~ 240` 截断 + 1 天 TTL 控制；snapshot 基线 ~3,500 ops/s 为现状负载，K 线新增 ~240 ops/s 影响小 |
| WebSocket / SSE 现有逻辑误把新增 freq 频道广播给旧客户端 | 客户端按 `type` 字段过滤，不识别就忽略，无破坏性 |
| 现有 `/realtime/kline/{code}` 走 SETEX，新代码用 Stream | 两条路径并存，互不读取对方 key |

---

## 13. 实施步骤

1. **新增配置项**：`Settings.realtime_kline_history_ttl` / `realtime_kline_max_bars`；env 落位到 `realtime.env` 与 `adshare/.env`
2. **key 契约**：`realtime_keys.py` 新增 `REALTIME_KLINE_HIST_KEY` 常量
3. **worker 端**：`amazingdata/realtime.py` 的 `_handle_kline` 同步 XADD Stream（保留 SETEX），key 经 `_make_key` 生成
4. **新增 mapper**：`adshare/services/realtime_tushare_mapper.py`
5. **新增 handler**：`adshare/routers/tushare/realtime.py`（含 kwargs 签名适配）
6. **注册到统一入口**：`routers/tushare/__init__.py` 引入 + `include_router`
7. **配置默认值**：把 `REALTIME_KLINE_PERIODS` 默认从 `["min1"]` 扩展到 5 周期
8. **单元测试 + 集成测试**
9. **灰度验证**：先 500 只 × 5 周期跑一个交易日，确认无订阅/带宽问题后扩全量
10. **更新 README** 端点列表

---

## 14. 后续扩展（v2+）

- 增量补全：盘前自动从 SDK 拉当日开盘前已生成的分钟 K 线，避免开盘后第一根延迟
- 跨日合并：盘后将当日 Stream 数据归档到 L3 历史仓的 `A_share/minute/{code}/` Parquet
- 多品种：可转债 / ETF / 港股通的 K 线（需 worker 侧订阅类型扩展）
- 限频与采样：超高活跃股票（>1万笔/分钟）降采样
- 客户端 SDK：在 `tushare.py` shim 中暴露 `ts.rt_k` / `ts.rt_min` 方法
- 清理 key 双层 "realtime" 前缀的历史瑕疵（需全链路一起改 + Redis 旧 key 兼容期）
