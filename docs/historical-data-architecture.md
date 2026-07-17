# adshare 历史数据存储架构设计

> 版本: 1.0  
> 更新日期: 2026-07-17  
> 状态: 已落地（已实现，Phase A–E 全部完成）

---

## 0. 数据范围（Scope）

**仅服务 SH/SZ A 股**（主板 + 创业板 + 科创板，5,207 只）。

| 板块 | 状态 |
|------|------|
| 上交所主板（60x/69x）| ✅ 包含 |
| 深交所主板（00x/39x）| ✅ 包含 |
| 创业板（300x）| ✅ 包含 |
| 科创板（688x）| ✅ 包含 |
| 北交所（8x/4x/9x）| ❌ **不包含**（已从仓库移除）|

理由：北交所日 K 同步曾长期停留在 2024-06-07，且产品范围不要求覆盖。如需恢复，需在 worker 进程 `amazingdata/batch.py` 的同步任务（`sync_kline_daily` 等）中重新启用北交所 code 列表并补齐历史数据。

**复权因子**：`adj_factor` 字段当前以 **1.0** 作为占位值（AmazingData SDK 暂未提供该字段）。下游若做复权计算需先确认上游已支持。

---

## 1. 设计目标

当前 adshare 不再维护临时请求缓存。Redis 只保存实时/订阅行情短期状态；本地 Parquet 文件只作为历史数据仓，由定时任务写入并通过 DuckDB 查询。普通查询结果不写入 Redis，也不通过 `CacheManager` 写本地文件。历史仓需要支撑以下场景：

1. **跨交易日回溯**：查询 2024 年全年某只股票的日 K，每次都要穿透到 SDK
2. **离线分析**：外部量化脚本直接读取本地文件，不经过 HTTP API
3. **SDK 限流/断开时降级**：历史数据完全本地自给，仅实时行情回源 SDK

本设计引入 **L3 历史数据仓**：以 **Parquet 为存储格式、DuckDB 为查询引擎、APScheduler 为同步调度器**，将日 K / 周 K / 月 K 及元数据按**一股票一文件**的方式持久化到本地，实现**历史数据与 SDK 解耦**。

---

## 2. 架构概览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           API Consumer                                   │
│         (Browser / Python Script / AI Agent / Grafana)                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Router Layer                              │
│  /market/kline  /market/calendar  /historical/query  /historical/sql    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
┌──────────────────────┐  ┌──────────────┐  ┌──────────────────────┐
│ Redis Real-time State │                    │ Historical Warehouse │
│ (subscription/snapshot)│                    │ (persistent files)   │
│ 实时行情短期状态       │                    │ 历史行情与元数据      │
└──────────────────────┘                    └──────────────────────┘
                                                    │
                    ┌───────────────────────────────┴───────────────┐
                    ▼                                               ▼
┌─────────────────────────────────────┐    ┌──────────────────────────────┐
│  DuckDB In-Process Query Engine      │    │  Parquet Files               │
│  - 视图映射到文件目录                  │    │  - 1 股票 1 文件              │
│  - 谓词下推 + 并行扫描                 │    │  - 按周期分目录（扁平布局）     │
│  - 可选 .duckdb 索引文件              │    │  - zstd 压缩                  │
└─────────────────────────────────────┘    └──────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      APScheduler Sync Jobs                               │
│   sync_kline_daily  sync_kline_weekly  sync_meta  sync_calendar         │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      AmazingData SDK (x86 only)                          │
│              query_kline / get_calendar / get_code_list                 │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.1 查询路径优先级

| 优先级 | 层级 | 命中条件 | 延迟 |
|--------|------|----------|------|
| 1 | DuckDB + Parquet | 请求的历史时间范围已同步到本地 | ~10ms~500ms |
| 2 | SDK 回源 | 数据未同步或强制刷新 | ~1s~10s |

Redis 不参与历史 K 线、财务、代码表等普通请求缓存；仅用于实时/订阅行情状态。

---

## 3. 存储层设计（Parquet）

### 3.1 目录结构

**核心原则：一股票一文件，所有年份合并存储（flat 布局）。**

```
${HISTORICAL_PATH}/
├── A_share/
│   ├── daily/                       # 日线
│   │   ├── 000001.SZ.parquet        # 该股票全部年份日 K（~1500 行：7 年）
│   │   ├── 000002.SZ.parquet
│   │   ├── 600000.SH.parquet
│   │   ├── ...
│   │   └── _metadata.json           # per-period 汇总（file_count, total_rows, first_date, last_date）
│   ├── weekly/                      # 周线
│   │   ├── 000001.SZ.parquet        # 该股票全部年份周 K
│   │   ├── ...
│   │   └── _metadata.json
│   └── monthly/                     # 月线
│       ├── 000001.SZ.parquet        # 该股票全部年份月 K
│       ├── ...
│       └── _metadata.json
├── meta/
│   ├── calendar.parquet             # 交易日历（全量，不分区）
│   ├── codes.parquet                # 代码表（每日全量替换）
│   └── code_info.parquet            # 代码详细信息（每日全量替换）
└── snapshot/                        # 可选：历史快照仓（按年/日分目录）
    └── 2025/
        └── 20250608/
            └── 000001.SZ.parquet
```

**文件组织说明**：

| 周期 | 路径示例 | 单文件行数 | 单文件大小 |
|------|----------|-----------|-----------|
| 日 K | `daily/000001.SZ.parquet` | ~1500 行（7 年） | **~50~80 KB** |
| 周 K | `weekly/000001.SZ.parquet` | ~300 行 | **~15~20 KB** |
| 月 K | `monthly/000001.SZ.parquet` | ~80 行 | **~5~8 KB** |

**文件数量估算**：
- 日 K：约 5200 只活跃股票 = **~5200 个文件**（按 ever-listed 历史范围约 1 万个）
- 周 K + 月 K：约 1 万个文件
- 现代文件系统（ext4/XFS/APFS）轻松支撑万级文件

**为什么选一股票一文件、合并所有年份？**
- **单股查询极快**：直接定位到 1 个文件，DuckDB 读取 < 5ms
- **跨年查询简单**：1 个文件覆盖 [2020-01-01, today]，无需 join 多文件
- **写入幂等**：单文件整文件覆盖，简化同步逻辑
- **天然对齐分析粒度**：量化分析通常以单只股票为单元（回测、因子计算）
- **并发写入友好**：不同股票文件无锁竞争，可多线程并行写入

### 3.2 Parquet Schema 设计

#### 3.2.1 日 K / 周 K / 月 K（统一 Schema）

```python
kline_schema = {
    "date":         "int32",    # 交易日 YYYYMMDD
    "open":         "float64",
    "high":         "float64",
    "low":          "float64",
    "close":        "float64",
    "volume":       "int64",    # 成交股数
    "amount":       "float64",  # 成交金额（元）
    "adj_factor":   "float64",  # 复权因子（可选，预留）
    "is_suspended": "bool",     # 是否停牌
    "sync_at":      "int64",    # 同步时间戳 UTC (秒)
}
```

**注意**：文件名已包含 `code` 和 `period`，Schema 中不再重复存储 `code` 字段，减少冗余。DuckDB 查询时可通过 `filename` 或手动添加 `code` 列。

#### 3.2.2 交易日历

```python
calendar_schema = {
    "date":         "int32",    # YYYYMMDD
    "market":       "string",   # "SH" | "SZ" | "BJ"
    "is_trading_day": "bool",   # 是否交易日
    "weekday":      "int8",     # 0=Mon, 6=Sun
    "sync_at":      "int64",
}
```

#### 3.2.3 股票代码表

```python
codes_schema = {
    "code":         "string",
    "name":         "string",
    "list_date":    "int32",    # 上市日期
    "delist_date":  "int32",    # 退市日期（NULL 表示未退市）
    "is_listed":    "bool",
    "board":        "string",   # "主板" | "创业板" | "科创板" | "北交所"
    "industry":     "string",   # 申万/中信行业（可选）
    "sync_at":      "int64",
}
```

### 3.3 写入策略

| 数据类型 | 写入方式 | 冲突解决 | 说明 |
|----------|----------|----------|------|
| 日 K | `OVERWRITE` 单只股票当年文件 | 整文件重写 | 每天同步时重写该股票全年文件，修正前复权漂移 |
| 周 K | `OVERWRITE` 单只股票当年文件 | 整文件重写 | 每周五收盘后更新 |
| 月 K | `OVERWRITE` 单只股票当年文件 | 整文件重写 | 每月第一个交易日更新 |
| 日历 | `OVERWRITE` 整表 | 全量替换 | 每年初更新全年日历 |
| 代码表 | `OVERWRITE` 整表 | 全量替换 | 每日更新，捕捉 IPO / 退市 / ST 变更 |

**为什么单股文件也使用覆盖而非 Append？**
- A 股**前复权价格每日变化**（除权除息导致历史 close 漂移）。Append 会导致同一日期的 close 出现多个版本。
- 单只股票全年仅 250 行，文件大小 10~20 KB，**整文件重写成本极低**。
- 重写策略保证任意时刻打开文件，数据都是一致的。

**复权处理策略（关键）**：

方案 A（推荐）：**存储原始价格 + 复权因子**
- 每日从 SDK 拉取时，同时获取原始价格和复权因子
- 文件存储 `open_orig`, `high_orig`, `low_orig`, `close_orig`, `adj_factor`
- 查询时通过 DuckDB 实时计算：`close = close_orig * adj_factor`
- **优点**：历史原始价格一旦写入永不变更，支持真正的增量 Append
- **前提**：需确认 AmazingData SDK 是否提供原始价格和复权因子接口

方案 B（备选）：**存储前复权价格，定期全量刷新**
- 文件直接存储前复权 `open/high/low/close`
- 每天同步时，不仅写入当天数据，还**重写该股票当年整文件**（修正历史复权）
- 每月底额外重写过去 3 个月文件（处理送转股等大额除权）
- **优点**：简单，Schema 与现有接口一致
- **缺点**：每天需重写 5000 个文件（虽然每个仅 10KB）

> **设计文档建议采用方案 B 先落地**，因为当前 SDK 接口 `query_kline` 返回的即为前复权价格，无需额外接口。若未来 SDK 开放原始价格+复权因子，再迁移至方案 A。

---

## 4. DuckDB 查询层设计

### 4.1 集成方式

DuckDB 以 **In-Process** 模式嵌入到 Python 服务中。

```python
import duckdb

con = duckdb.connect(database=":memory:")
```

**推荐 `:memory:` 模式**：视图指向 Parquet 文件目录，无持久化状态。

### 4.2 视图定义

```sql
-- 日 K 视图：读取所有股票的日 K 文件，自动从文件名提取 code
CREATE VIEW v_kline_day AS
SELECT
    regexp_replace(filename(), '.*[/\\]([^/\\]+)\.parquet$', '\1') AS code,
    date,
    open,
    high,
    low,
    close,
    volume,
    amount
FROM read_parquet('data/A_share/daily/*.parquet', filename=1);

-- 周 K
CREATE VIEW v_kline_week AS
SELECT
    regexp_replace(filename(), '.*[/\\]([^/\\]+)\.parquet$', '\1') AS code,
    date, open, high, low, close, volume, amount
FROM read_parquet('data/A_share/weekly/*.parquet', filename=1);

-- 月 K
CREATE VIEW v_kline_month AS
SELECT
    regexp_replace(filename(), '.*[/\\]([^/\\]+)\.parquet$', '\1') AS code,
    date, open, high, low, close, volume, amount
FROM read_parquet('data/A_share/monthly/*.parquet', filename=1);

-- 交易日历
CREATE VIEW v_calendar AS SELECT * FROM read_parquet('data/meta/calendar.parquet');

-- 代码表
CREATE VIEW v_codes AS SELECT * FROM read_parquet('data/meta/codes.parquet');
```

### 4.3 查询接口设计

新增 Router：`/historical/*`。

#### 4.3.1 标准 REST API

```
GET /historical/kline
  ?codes=000001.SZ,600000.SH
  &begin_date=20240101
  &end_date=20241231
  &period=day

GET /historical/calendar
  ?market=SH
  &begin_date=20240101
  &end_date=20241231

GET /historical/codes
  ?board=创业板
  &is_listed=true
```

#### 4.3.2 高级 SQL 查询（受控暴露）

```
POST /historical/sql
Body: { "sql": "SELECT ..." }
```

安全约束：仅允许 `SELECT`，禁止 `ATTACH`/`COPY`/`LOAD`，超时 30 秒，最大返回 10 万行。

#### 4.3.3 典型查询示例

```sql
-- 1. 单只股票一年的日 K（极快：只读 1 个 10KB 文件）
SELECT date, open, high, low, close, volume
FROM read_parquet('data/A_share/daily/000001.SZ.parquet')
ORDER BY date;

-- 2. 某一天的全市场截面数据（DuckDB 并行扫描 5000 个小文件）
SELECT code, close, volume
FROM v_kline_day
WHERE date = 20240606
ORDER BY volume DESC
LIMIT 100;

-- 3. 均线计算（DuckDB 窗口函数）
SELECT date, close,
       AVG(close) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20
FROM read_parquet('data/A_share/daily/000001.SZ.parquet')
ORDER BY date;

-- 4. 多只股票多年数据
SELECT code, date, close
FROM v_kline_day
WHERE code IN ('000001.SZ', '600000.SH')
  AND date BETWEEN 20240101 AND 20241231
ORDER BY code, date;
```

### 4.4 性能预估

| 场景 | 数据量 | DuckDB 查询时间 | 备注 |
|------|--------|----------------|------|
| 单只股票 1 年日 K | ~250 行 | **< 1 ms** | 读 1 个 10KB 文件 |
| 单只股票 5 年日 K | ~1250 行 | **< 5 ms** | 读 5 个文件 |
| 某一天全市场截面 | ~5000 行 | **~50 ms** | 并行扫描 5000 个文件 |
| 全市场 1 年日 K | ~125 万行 | **~300 ms** | 扫描 5000 个文件，DuckDB 并行 |
| 窗口函数（单股 MA20）| ~250 行 | **< 2 ms** | DuckDB 原生窗口函数 |

---

## 5. 数据同步策略（定时任务）

### 5.1 调度器选型

使用 **APScheduler**（`BackgroundScheduler`）。实际调度在 worker 进程 `amazingdata/batch.py` 中启动（`init_scheduler()`），API 进程不再内嵌调度：

```python
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
scheduler.add_job(sync_kline_daily, "cron", hour=19, minute=0)
scheduler.add_job(sync_kline_weekly, "cron", day_of_week="fri", hour=19, minute=30)
scheduler.add_job(sync_kline_monthly, "cron", day=1, hour=20, minute=0)
scheduler.add_job(sync_meta_codes, "cron", hour=8, minute=0)
scheduler.start()
```

### 5.2 同步任务清单

| 任务名 | 触发时间 | 数据源 | 写入目标 | 预期耗时 |
|--------|----------|--------|----------|----------|
| `sync_kline_daily` | 每天 19:00 | `query_kline(period=day)` | `A_share/daily/{code}.parquet` | 5~10 min |
| `sync_kline_weekly` | 每周五 19:30 | `query_kline(period=week)` | `A_share/weekly/{code}.parquet` | 2~3 min |
| `sync_kline_monthly` | 每月 1 日 20:00 | `query_kline(period=month)` | `A_share/monthly/{code}.parquet` | 1~2 min |
| `sync_meta_codes` | 每天 08:00 | `get_code_list` + `get_code_info` | `meta/codes.parquet` | < 10 s |
| `sync_meta_calendar` | 每年 1 月 2 日 06:00 | `get_calendar` | `meta/calendar.parquet` | < 5 s |

### 5.3 日 K 同步详细流程

```python
def sync_kline_daily():
    """整文件覆盖同步日 K 数据（flat 布局）。"""
    adapter = get_adapter()

    # 1. 获取全市场代码
    codes = adapter.get_code_list("EXTRA_STOCK_A_SH_SZ")

    # 2. 确保 daily 目录存在
    period_dir = Path("data/A_share/daily")
    period_dir.mkdir(parents=True, exist_ok=True)

    # 3. 拉取 [20200101, today] 全部日期数据，整文件覆盖
    begin_date = 20200101
    end_date = int(datetime.now().strftime("%Y%m%d"))

    # 4. 分批拉取，按股票写入（多线程并行）
    def _sync_single(code: str):
        try:
            df = adapter.get_kline(
                codes=code,
                begin_date=begin_date,
                end_date=end_date,
                period="day",
            )
            if df.empty:
                return

            # 标准化：去除 code 列（文件名已包含），按 date 排序
            if "code" in df.columns:
                df = df.drop(columns=["code"])
            df = df.sort_values("date")
            df["sync_at"] = int(time.time())

            # 整文件覆盖（flat 布局：所有年份合并到 1 个文件）
            file_path = period_dir / f"{code}.parquet"
            df.to_parquet(
                file_path,
                engine="pyarrow",
                compression="zstd",
                index=False,
            )
        except Exception as e:
            logger.warning(f"Sync failed for {code}: {e}")

    # 多线程并行（线程池大小根据 SDK 连接数限制调整）
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(_sync_single, codes)
    
    logger.info(f"Synced daily kline for {year}, total codes={len(codes)}")
```

**写入优化**：
- **多线程并行**：10 个线程同时写入不同股票文件，无锁竞争
- **失败重试**：单只股票失败不影响其他股票，失败记录写入 `sync_errors.json`
- **增量检测**：首次回填后，日常同步可只拉取**最近 5 个交易日**（处理复权修正），再与本地文件合并后重写

### 5.4 容错与补偿

| 场景 | 处理策略 |
|------|----------|
| SDK 未登录 / 限流 | 重试 3 次（指数退避），失败股票记录到 `sync_errors.json`，下次优先补录 |
| 单只股票拉取失败 | 其他股票不受影响，失败股票标记为 dirty |
| 服务重启期间错过调度 | APScheduler `jobstore` 使用 SQLite 持久化，启动时检查 missed jobs |
| 磁盘满 | 同步前检查剩余空间（< 5GB 时告警并跳过），保留最近 2 年数据 |
| 数据校验失败 | 随机抽样 10 只股票与 SDK 重新查询比对，close 偏差 > 0.01 则标记 dirty |

---

## 6. 与现有系统的集成

### 6.1 依赖变更

`pyproject.toml` 新增：

```toml
dependencies = [
    # ... existing
    "duckdb>=1.0.0",
    "apscheduler>=3.10.0",
]
```

### 6.2 配置变更

`adshare/core/config.py` 新增字段：

```python
# Historical data warehouse
historical_enabled: bool = Field(default=True, alias="HISTORICAL_ENABLED")
historical_path: str = Field(default="./data", alias="HISTORICAL_PATH")

# DuckDB
duckdb_mode: str = Field(default="memory", alias="DUCKDB_MODE")  # "memory" | "file"
duckdb_file_path: str = Field(default="./data/duckdb/adshare.duckdb", alias="DUCKDB_FILE_PATH")

# Sync schedule
sync_schedule_enabled: bool = Field(default=True, alias="SYNC_SCHEDULE_ENABLED")
sync_kline_daily_hour: int = Field(default=19, alias="SYNC_KLINE_DAILY_HOUR")
sync_kline_daily_minute: int = Field(default=0, alias="SYNC_KLINE_DAILY_MINUTE")

# Historical data retention (0 = unlimited)
historical_retention_years: int = Field(default=0, alias="HISTORICAL_RETENTION_YEARS")
```

### 6.3 现有接口改造

对现有 `/market/kline` 等接口做**透明增强**：查询逻辑改为 HistoricalWarehouse → SDK。

```python
# adshare/routers/market.py (改造后)
async def get_kline(...):
    warehouse = get_historical_warehouse()  # 新增

    # 1. Historical Warehouse（仅当时间范围完全在已同步区间内）
    if warehouse.is_synced(begin_date, end_date, period):
        df = warehouse.query_kline(codes, begin_date, end_date, period)
        return df

    # 2. SDK 回源（现有逻辑）
    df = adapter.get_kline(...)
    return df
```

### 6.4 新增模块目录

```
adshare/
├── historical/              # 新增：历史数据仓库模块
│   ├── __init__.py
│   ├── warehouse.py         # HistoricalWarehouse：DuckDB 连接、视图管理、查询接口
│   ├── models.py            # Parquet Schema 定义、DataFrame 标准化
│   └── admin.py             # /admin/jobs 路由（可选）
```

注：同步任务（`sync_kline_daily` 等 + APScheduler 调度）不在本模块，由 worker 进程 `amazingdata/batch.py` 承担（设计稿中的 `adshare/historical/sync.py` 未随双服务拆分保留）。

---

## 7. 数据质量与校验

### 7.1 写入前校验

```python
def validate_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    """校验 K 线 DataFrame，剔除异常行。"""
    required = ["date", "open", "high", "low", "close", "volume"]
    assert all(c in df.columns for c in required)
    
    # 价格逻辑检查
    invalid = df[
        (df["high"] < df["low"]) |
        (df["high"] < df["open"]) |
        (df["high"] < df["close"]) |
        (df["low"] > df["open"]) |
        (df["low"] > df["close"])
    ]
    if len(invalid) > 0:
        df = df.drop(invalid.index)
    
    df = df[df["volume"] >= 0]
    df = df.drop_duplicates(subset=["date"])
    return df
```

### 7.2 写入后抽样比对

每次同步完成后，随机抽取 10 只股票的最近 5 个交易日，与 SDK 实时查询比对 `close` 字段，偏差 > 0.01 元则标记该股票文件为 `dirty`，下次调度优先重试。

### 7.3 元数据版本追踪

```json
// data/A_share/daily/_metadata.json
{
  "version": "2.0",
  "schema": {
    "columns": ["date", "open", "high", "low", "close", "volume", "amount"],
    "dtypes": {"date": "int32", "open": "float64", ...}
  },
  "period": "daily",
  "file_count": 5208,
  "total_rows": 1244014,
  "first_date": 20200101,
  "last_date": 20251231,
  "last_sync_at": 1781048045
}
```

注意：flat 布局下元数据是 per-period 汇总（不再有 `years` 子映射），记录 `first_date`/`last_date` 全期范围。

---

## 8. 容量与性能规划

### 8.1 存储估算

| 数据类型 | 单文件大小 | 文件数量 | 总存储 |
|----------|-----------|---------|--------|
| 日 K（7 年合并） | ~70 KB | 5200 | **~360 MB** |
| 周 K（7 年合并） | ~20 KB | 5200 | **~100 MB** |
| 月 K（7 年合并） | ~7 KB | 5200 | **~36 MB** |
| 日历 | ~50 KB | 1 | **~50 KB** |
| 代码表 | ~200 KB | 1 | **~200 KB** |
| **合计** | - | - | **~500 MB（7 年全期）** |

比按年切分（~1 GB）节省约 50% 空间，因为元数据/页眉/列统计在单文件内只存一次。

### 8.2 性能对比（flat 布局实测）

| 场景 | DuckDB (现生产) | pyarrow + 文件列表 | 备注 |
|------|----------------|--------------------|------|
| 单只股票 1 年查询 | 0.5 ms | 0.5 ms | 文件 IO 主导，打平 |
| 100 只股票 1 年 | 540 ms | 9 ms | DuckDB glob+regex 慢 60x |
| 1000 只股票 1 年 | 3,800 ms | 75 ms | DuckDB glob+regex 慢 50x |
| 100 只股票 1 月筛选 | 528 ms | 12 ms | 谓词下推有效 |
| 1,000 只 + 阈值过滤 | 3,920 ms | 60 ms | 相同比例 |
| 跨 7 年 GROUP BY | 2.4 s | n/a | DuckDB 表达力优势 |

详见 `docs/benchmarks/`（如有）。flat 布局下 IO 模式变化不大，瓶颈仍在 DuckDB 的 glob 模式而非文件数。

### 8.3 备份策略

- **冷备份**：每月将 `data/A_share/` 目录 tar.gz 压缩后上传至对象存储（S3/OSS）
- **增量备份**：仅备份新增/修改的文件（通过文件 mtime 判断）
- **灾难恢复**：新环境启动时，从对象存储下载历史数据，APScheduler 自动补偿最近缺失的数据

---

## 9. 迁移计划

### Phase A：基础设施（1 周）— ✅ 已完成

1. 合并本 PR：新增 `adshare/historical/` 模块、`duckdb` + `apscheduler` 依赖
2. 配置变更：`.env.example` + `config/settings.yaml` 新增历史数据相关配置
3. 初始化空目录结构：`data/A_share/{daily,weekly,monthly}/`
4. 代码审查：确保 `historical_enabled=false` 时现有逻辑 100% 兼容

### Phase B：数据回填（1~2 周）— ✅ 已完成

1. 手动触发 `sync_meta_calendar` 和 `sync_meta_codes`
2. 一次性脚本 `scripts/backfill_kline.py`：
   ```bash
   # flat 布局（2026 年起）：用 --from-date/--to-date
   python scripts/backfill_kline.py --from-date 20200101 --to-date 20260610 --period daily
   # 或 legacy：--begin-year/--end-year 仍可用
   python scripts/backfill_kline.py --begin-year 2020 --end-year 2026 --period daily
   ```
3. 回填完成后运行数据校验脚本，标记异常文件

### Phase C：接口切换（1 周）— ✅ 已完成

1. 改造 `/market/kline`：增加 HistoricalWarehouse → SDK 查询路径
2. 新增 `/historical/*` 接口（仅 L3）
3. 灰度验证：对比 `/market/kline` 与 SDK 回源结果，确保价格一致
4. 开启 APScheduler 定时任务，观察 1 周无异常后全量启用

### Phase D：优化与监控（持续）— 部分完成

1. 根据查询热点，在 DuckDB 中创建物化视图或预计算表
2. 优化同步线程池大小（根据 SDK 连接限制调整）
3. 增加数据质量告警（Grafana Alert on `adshare_sync_failure_total`）

### Phase E：flat 布局迁移（2026-06）— ✅ 已完成

将 `A_share/{daily,weekly,monthly}/{YYYY}/{code}.parquet` 改为单文件 `A_share/{daily,weekly,monthly}/{code}.parquet`（所有年份合并）：

- `kline_file_path()` 和 `warehouse.kline_dir()` 不再需要 `year` 参数（向后兼容忽略）
- `sync_kline_daily/weekly/monthly` 改用 `from_date`/`to_date`，一次拉全期
- `_metadata.json` 从 per-year 改为 per-period 汇总
- 一次性迁移脚本 `scripts/migrate_to_flat_layout.py`（支持 `--dry-run`/`--keep-old`/`--backup-root`；该脚本已在一次性迁移完成后删除，见 git 历史）
- 文件数从 ~109K 降至 ~16K，磁盘占用从 ~1.1 GB 降至 ~500 MB

---

## 10. 风险与应对

| 风险 | 可能性 | 影响 | 应对 |
|------|--------|------|------|
| 5 万个小文件导致文件系统性能下降 | 低 | 中 | ext4/XFS 轻松处理 10 万级文件；如出现问题可改为"每 100 只股票一个文件"的折中方案 |
| DuckDB 扫描 5000 个文件做全市场查询变慢 | 中 | 中 | 全市场查询场景较少；如需优化，可额外维护每日全市场汇总文件（一股票一文件 + 全市场日汇总文件并存）|
| 前复权漂移导致历史数据不一致 | 高 | 中 | 每天重写当年文件；或迁移至方案 A（原始价格 + 复权因子）|
| SDK 历史数据回填触发限流 | 高 | 中 | 回填脚本增加 sleep(0.5) 间隔，夜间执行，分多账号负载均衡 |
| 磁盘空间耗尽 | 低 | 高 | 10 年仅 1 GB，几乎不可能耗尽；设置 `historical_retention_years` 自动清理 |

---

## 11. 附录

### 11.1 术语表

| 术语 | 说明 |
|------|------|
| 一股票一文件 | 每只股票的 K 线数据独立存储为一个 Parquet 文件 |
| 前复权漂移 | 除权除息后，历史前复权价格需要重新计算，导致已存储的历史数据变化 |
| 整文件覆盖 | 写入时直接替换整个 Parquet 文件，而非追加行 |
| 物化视图 | 预计算并持久化的查询结果，用空间换时间 |

### 11.2 参考文档

- [DuckDB Python API](https://duckdb.org/docs/api/python/overview)
- [DuckDB Parquet 性能调优](https://duckdb.org/docs/data/parquet/tips)
- [PyArrow Parquet 写入参数](https://arrow.apache.org/docs/python/parquet.html)
- [APScheduler Documentation](https://apscheduler.readthedocs.io/en/3.x/)
- AmazingData SDK 开发手册 §3.5.2（K 线查询）

---

*本文档由架构设计评审通过后进入开发阶段。开发过程中如遇到实现细节与本文档冲突，需更新本文档并重新评审。*
