# adshare 实时数据 tushare 扩展设计

> 版本: 0.1.0-draft  
> 更新日期: 2026-07-16  
> 状态: 设计评审中（待开发）

---

## 0. 背景与动机

adshare 当前已具备实时行情链路：

- **Worker 进程** 通过 AmazingData SDK 的 `SubscribeData` 订阅 Level-1 快照、指数快照、实时 K 线（`min1`）
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

1. **覆盖范围**：A 股 SH+SZ 全部约 5,400 只股票。
2. **端点对等**：
   - `POST /tushare` 统一入口支持 ``api_name=rt_k``
   - `POST /tushare` 统一入口支持 `api_name=rt_min`
3. **REST 直连**：同步暴露 `POST /tushare/realtime/rt_k` 与 `POST /tushare/realtime/rt_min`
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
- **当前 adshare 路径**：`realtime:quote/{code}` Redis key + `adshare:realtime:quote` 频道

### 2.2 [`rt_min`](https://tushare.pro/document/2?doc_id=374)

- **含义**：实时分钟 K 线（最近若干根）
- **频率**：min1 每分钟推送一次；min5 每 5 分钟一次；以此类推
- **字段**：`ts_code / freq / trade_time / open / high / low / close / volume / amount`
- **数据源映射**：AmazingData SDK `OnKLine` + `Period.min{1,3,5,10,15,30,60,120}.value`
- **当前 adshare 路径**：`realtime:kline:{period}/{code}` Redis key + `adshare:realtime:kline:{period}` 频道

---

## 3. 现状能力盘点

### 3.1 Worker 侧

文件：`amazingdata/realtime_publisher.py`

- 已注册三类回调：
  - `on_snapshot` → `realtime:quote/{code}` + `adshare:realtime:quote`
  - `on_index_snapshot` → `realtime:index/{code}` + `adshare:realtime:index`
  - `on_kline`（按 `Settings.realtime_kline_periods`）→ `realtime:kline:{period}/{code}` + `adshare:realtime:kline:{period}`
- 代码表来源：`meta/codes.parquet`（避免和 SubscribeData 抢 TGW 单连接）
- 写入模型：**单 key 覆盖**（`set_realtime_market` → `SETEX`），不累积历史

### 3.2 API 侧

文件：
- `adshare/routers/realtime.py` — REST + WS + SSE
- `adshare/services/realtime_broadcast.py` — Redis Pub/Sub 监听 → WS/SSE 广播
- `adshare/core/cache.py` — `get_realtime_market` / `set_realtime_market`
- `adshare/core/realtime_keys.py` — key/channel 契约

- `RealtimeBroadcastService.start()` 已经按 `realtime_kline_periods` 动态拼频道，扩展无需改
- WS/SSE payload 已是 dict 直传，新增 `type=tick/kline` 无需改广播逻辑

### 3.3 已有 tushare 路由骨架

文件：`adshare/routers/tushare/`

- `__init__.py`：统一入口 `POST /tushare`，通过 `stock.HANDLERS or index.HANDLERS` 分发
- `stock.py`：注册了 `daily / weekly / monthly / stock_basic / trade_cal / adj_factor / suspend_d / limit_list`
- `index.py`：仅占位
- `common.py`：提供 `df_to_tushare_payload` / `filter_fields` / 参数解析

---

## 4. 数据规模测算

### 4.1 假设

- 标的数：A 股 SH+SZ = 5,400 只（沿用 worker 现有 `EXTRA_STOCK_A_SH_SZ`）
- 交易时段：4 小时 / 日 = 240 分钟
- 每根 K 线 payload：JSON+pickle ≈ 220 bytes
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

- min1：5400 推送/分钟 = **90 push/s**（平均），峰值 200+ push/s
- min5：18 push/s
- min15：6 push/s
- min30：3 push/s
- min60：1.5 push/s
- **合计 ~120 ops/s**，远低于 Redis 单机能力（10 万 ops/s）

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
│              pro.rt_min(...)  /  pro.rt_k(...)           │
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
│  │       ├─ rt_k          → handle_rt_k()           │  │
│  │       └─ rt_min          → handle_rt_min()                  │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                  Realtime Tushare Mapper                          │
│        _quote_payload() / _kline_payload()                        │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                     CacheManager                                  │
│         get_realtime_market(REALTIME_QUOTE_KEY, code)              │
│         XREVRANGE realtime:kline:{freq}:hist/{code} ...            │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                          Redis                                    │
│   Key:  realtime:quote/{code}                                     │
│   Stream: realtime:kline:{freq}:hist/{code}    ← 新增结构         │
└──────────────────────────────────────────────────────────────────┘
                                ▲
                                │  XADD / SETEX
                                │
┌──────────────────────────────────────────────────────────────────┐
│              amazingdata realtime (SubscribeData)                   │
│   on_snapshot → REALTIME_QUOTE_KEY                                │
│   on_kline    → Stream realtime:kline:{freq}:hist/{code}          │
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

### 6.1 复用：snapshot（保持现状）

- Redis Key：`adshare:realtime:quote/{code}`
- 写入方：`RealtimePublisher._handle_snapshot`（无改动）
- 读取方：`handle_rt_k` 新增

### 6.2 新增：K 线 Stream

每只股票每个频率一个 Redis Stream：

- Key：`adshare:realtime:kline:{freq}:hist/{code}`（沿用现有 key 命名约定，新增 `:hist` 子命名空间）
- 写入：`XADD key * trade_time {ms} data {json}`
- 读取：`XREVRANGE key + - COUNT N`（拉最近 N 根）
- TTL：`EXPIRE key 86400`，盘后自然过期
- 上限：`XADD MAXLEN ~ 240`（min1 单只单日根数）

#### 与现有 SETEX 的兼容性

现有 `_handle_kline` 走 `set_realtime_market` → `SETEX`，下游 `/realtime/kline/{code}` 直接读单 key。

为避免破坏现有调用方，**保留单 key 行为作为 `current`**，同时新增 `XADD` 累积。两种存储并行：

```
key:  adshare:realtime:kline:min1/{code}        (SETEX, 最新 1 根，5 分钟 TTL)
key:  adshare:realtime:kline:min1:hist/{code}   (STREAM, 历史累积，1 天 TTL)
```

下游 `/realtime/kline/{code}` 走 SETEX 不变；tushare `rt_min` 走 `:hist/{code}` Stream。

### 6.3 Pub/Sub 频道（不变）

- `adshare:realtime:quote`（已存在）
- `adshare:realtime:kline:{freq}`（已存在）

`RealtimeBroadcastService.start()` 已经动态拼所有 `realtime_kline_periods` 频道，扩展 freq 列表无需改代码。

---

## 7. 模块设计

### 7.1 新增文件

```
adshare/
├── routers/
│   └── tushare/
│       └── realtime.py              # 新增：rt_k / rt_min handler + REST 路由
├── services/
│   └── realtime_tushare_mapper.py   # 新增：snapshot / kline → tushare 字段映射
amazingdata/
└── realtime_publisher.py            # 修改：_handle_kline 增加 XADD Stream（保留 SETEX）
```

### 7.2 配置变更

`adshare/core/config.py` 的 `Settings`：

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

### 7.3 worker 端改动

`amazingdata/realtime_publisher.py`：

```python
# _handle_kline 内部：除了原来的 set_realtime_market(SETEX)
# 新增：
stream_key = f"adshare:realtime:kline:{period_str}:hist/{code}"
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

#### handle_rt_min

- 入参：`ts_code`（必填）、`freq`（必填，1/5/15/30/60 MIN）、可选 `start_time` / `end_time` / `limit`
- freq 映射：tushare `"1MIN"` → `"min1"`、`"5MIN"` → `"min5"` ...
- 出参：最多 N 行 K 线，按 `trade_time` 升序
- 数据源：`XREVRANGE adshare:realtime:kline:{freq}:hist/{code} + - COUNT {limit}`
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

```python
@router.post("/realtime/quote")
@router.get("/realtime/quote")
async def tushare_rt_k(...): ...

@router.post("/realtime/rt_min")
@router.get("/realtime/rt_min")
async def tushare_rt_min(...): ...
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

`.env`：

```bash
# 5 周期全开（原默认只有 min1）
REALTIME_KLINE_PERIODS=["min1","min5","min15","min30","min60"]

# K 线历史 TTL：1 天（盘后清零）
REALTIME_KLINE_HISTORY_TTL=86400

# 单只股票单个 freq Stream 最大保留 240 根（min1 全天根数）
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
  - `POST /tushare` 统一入口分发（``api_name=rt_k`` / `rt_min`）

### 11.3 端到端

- 启动 worker + API
- 通过 ws://.../realtime/ws 订阅触发 Redis 写入
- 通过 POST /tushare 验证读取

---

## 12. 风险与权衡

| 风险 | 缓解 |
|---|---|
| `_SDK_CALL_LOCK` 在 K 线密集推送时仍可能阻塞 | K 线订阅回调本身只做 XADD + SETEX，不调用 SDK，锁影响小 |
| TGW 单连接限制下 SubscribeData 已占用连接 | 不影响，复用现有订阅 |
| Redis 内存占用 ~412 MB（Stream 版）| 通过 `MAXLEN ~ 240` 截断 + 1 天 TTL 控制 |
| WebSocket / SSE 现有逻辑可能误把新增 freq 频道广播给旧客户端 | 客户端按 `type` 字段过滤，不识别就忽略，无破坏性 |

| 现有 `/realtime/kline/{code}` 走 SETEX，新代码用 Stream | 两条路径并存，互不读取对方 key |

---

## 13. 实施步骤

1. **新增配置项**：`Settings.realtime_kline_history_ttl` / `realtime_kline_max_bars`
2. **worker 端**：`_handle_kline` 同步 XADD Stream（保留 SETEX）
3. **新增 mapper**：`adshare/services/realtime_tushare_mapper.py`
4. **新增 handler**：`adshare/routers/tushare/realtime.py`
5. **注册到统一入口**：`routers/tushare/__init__.py` 引入 + `include_router`
6. **配置默认值**：把 `REALTIME_KLINE_PERIODS` 默认从 `["min1"]` 扩展到 5 周期
7. **单元测试 + 集成测试**
8. **更新 README** 端点列表

---

## 14. 后续扩展（v2+）

- 增量补全：盘前自动从 SDK 拉当日开盘前已生成的分钟 K 线，避免开盘后第一根延迟
- 跨日合并：盘后将当日 Stream 数据归档到 L3 历史仓的 `A_share/minute/{code}/` Parquet
- 多品种：可转债 / ETF / 港股通的 K 线（需 worker 侧订阅类型扩展）
- 限频与采样：超高活跃股票（>1万笔/分钟）降采样
- 客户端 SDK：在 `tushare.py` shim 中暴露 `ts.rt_k` / `ts.rt_min` 方法
