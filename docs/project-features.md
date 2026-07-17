# adshare 项目功能手册

> 版本: 0.1.0  
> 适用对象: 前端开发者、AI Agent 开发者、数据分析师、运维工程师

---

## 1. 功能全景图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              客户端 (任意平台)                               │
│  (Vibe-Trading, ruo-cli, 浏览器, Jupyter, AI Agent ...)                    │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │ HTTP / WebSocket / SSE
┌─────────────────────────────────▼───────────────────────────────────────────┐
│                              adshare 服务层                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │  Market     │  │ Financial   │  │ Technical   │  │ Fundamental │        │
│  │  市场数据   │  │ 财务数据    │  │ 技术分析    │  │ 基本面分析  │        │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                         │
│  │ Factor      │  │ Health      │  │ Tushare     │                         │
│  │ 因子分析    │  │ 监控运维    │  │ 协议兼容    │                         │
│  └─────────────┘  └─────────────┘  └─────────────┘                         │
│  ┌─────────────┐  ┌─────────────┐                                          │
│  │ Realtime    │  │ Historical  │                                          │
│  │ 实时行情    │  │ 历史数仓    │                                          │
│  └─────────────┘  └─────────────┘                                          │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────────────┐
│                            数据与基础设施层                                  │
│  ┌──────────────────────────────────────┐  ┌──────────────────────────┐    │
│  │  AmazingData Workers (realtime/batch)│  │  Historical Warehouse    │    │
│  │  SDK 订阅与同步 (Linux/amd64 only)   │  │  Parquet + DuckDB        │    │
│  └──────────────────────────────────────┘  └──────────────────────────┘    │
│  ┌──────────────────────────────────────┐                                  │
│  │  Redis Real-time State               │                                  │
│  │  (subscription/snapshot only)         │                                  │
│  └──────────────────────────────────────┘                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 市场数据 (Market)

**路由前缀**: `/market`

### 2.1 证券代码表

| 端点 | 方法 | 说明 |
|------|------|------|
| `/codes` | GET | 获取全市场代码列表，支持沪深北 A 股、ETF、期货、期权等 |

**请求参数**:
- `security_type`: 代码类型，如 `EXTRA_STOCK_A`（默认）、`EXTRA_ETF`、`EXTRA_INDEX_A`

**响应示例**:
```json
{
  "security_type": "EXTRA_STOCK_A",
  "code_list": ["000001.SZ", "600000.SH", ...],
  "count": 5354,
  "data": ["000001.SZ", "600000.SH", ...]
}
```

**对应 SDK**: `BaseData.get_code_list()` — 见 AmazingData 开发手册 §3.5.2.2

---

### 2.2 K 线数据

| 端点 | 方法 | 说明 |
|------|------|------|
| `/kline` | GET | 标准 K 线查询，支持多代码、多周期 |
| `/kline/simple` | GET | 简化查询，仅需 `symbol` + `count`，自动推算日期范围 |

**周期支持**:

| 参数值 | 说明 | SDK Period Code |
|--------|------|-----------------|
| `tick` | Tick | 0 |
| `min1` | 1 分钟 | 10000 |
| `min5` | 5 分钟 | 10002 |
| `min15` | 15 分钟 | 10004 |
| `min30` | 30 分钟 | 10005 |
| `min60` | 60 分钟 | 10006 |
| `day` | 日线（默认） | 10008 |
| `week` | 周线 | 10009 |
| `month` | 月线 | 10010 |

**对应 SDK**: `MarketData.query_kline()` — 见开发手册 §3.5.4.2

---

### 2.3 快照数据

| 端点 | 方法 | 说明 |
|------|------|------|
| `/snapshot` | GET | 查询指定代码的最新 Level-1 快照 |

**字段覆盖**: open, high, low, close, volume, amount, bid_price, ask_price 等

**对应 SDK**: `MarketData.query_snapshot()` — 见开发手册 §3.5.4.1

---

### 2.4 证券基础信息

| 端点 | 方法 | 说明 |
|------|------|------|
| `/stock/basic` | GET | 个股详细资料：上市日期、退市日期、板块、上市状态 |

**对应 SDK**: `InfoData.get_stock_basic()` — 见开发手册 §3.5.2.9

---

### 2.5 交易日历

| 端点 | 方法 | 说明 |
|------|------|------|
| `/calendar` | GET | 查询指定市场的交易日历 |

- `market`: `SH`（上海，默认）、`SZ`（深圳）、`BJ`（北京）

**对应 SDK**: `BaseData.get_calendar()` — 见开发手册 §3.5.2.8

---

### 2.6 涨停榜

| 端点 | 方法 | 说明 |
|------|------|------|
| `/limit-up` | GET | 指定交易日涨停股票列表（基于日线 K 线计算） |
| `/limit-up/ladder` | GET | 涨停梯队（连板层级统计） |

**计算逻辑**:
- 优先读取本地历史仓的日线 K 线与代码元数据；缺失时回源 AmazingData，并将补齐数据写入本地 Parquet。
- 涨停价 = 前一交易日收盘价 × (1 + 涨幅限制比例)，四舍五入至分位后与当日收盘价比较。
- 主板: 10%；创业板: 20%；科创板: 30%。
- 自动过滤 ST/*ST（可配置 `exclude_st=false` 保留）

**⚠️ 注意**: 该端点会按本地历史仓覆盖情况补齐缺口数据，建议通过每日定时任务预先同步历史行情与元数据。

---

## 3. 财务数据 (Financial)

**路由前缀**: `/financial`

> **⚠️ 已禁用**: 财务三表同步已停用（HDF5 缓存占用过大且无人使用）。以下接口仅保留路径占位，
> 调用时直接返回 `503`（提示需要 AmazingData SDK worker 服务）。
> 如需恢复财务数据，可手动运行 `scripts/backfill_financial.py` 回填。

### 3.1 财务报表

| 端点 | 方法 | 说明 |
|------|------|------|
| `/statement` | GET | （已禁用）资产负债表 / 利润表 / 现金流量表 / 业绩快报 / 业绩预告 |

---

### 3.2 股东数据

| 端点 | 方法 | 说明 |
|------|------|------|
| `/shareholder` | GET | （已禁用）十大股东信息 |

---

## 4. 技术分析 (Technical)

**路由前缀**: `/technical`

### 4.1 指标列表

| 端点 | 方法 | 说明 |
|------|------|------|
| `/indicators` | GET | 返回全部 57 个指标的分类清单 |

### 4.2 单股分析

| 端点 | 方法 | 说明 |
|------|------|------|
| `/analyze` | GET | 对指定股票计算全部或指定类别/指定指标的技术指标 |

**查询参数**:
- `code`: 股票代码，如 `000001.SZ`
- `begin_date` / `end_date`: 日期范围 `YYYYMMDD`
- `indicator`: 指定单个指标，如 `MACD`（可选）
- `category`: 指定类别，如 `trend`（可选）

**指标分类**:

| 类别 | 英文名 | 指标数 | 代表指标 |
|------|--------|--------|----------|
| 超买超卖 | overbought_oversold | 14 | KDJ, RSI, WR, CCI, BIAS |
| 趋势型 | trend | 14 | MACD, DMI, DMA, TRIX, UOS |
| 能量型 | energy | 5 | CR, PSY, MASS, WAD |
| 成交量型 | volume | 10 | OBV, VR, VOLMA, VRSI |
| 均线型 | ma | 4 | MA, EXPMA, BBI, AMV |
| 路径型 | path | 6 | BOLL, ENE, MIKE, PBX, SAR |
| 其他 | other | 4 | ATR, CDP, ASI |

**实现特点**:
- 全部使用 **纯 pandas/numpy** 实现，不依赖 AmazingData 的 `TimeSeriesFunction`
- 因此该模块**可在任意平台运行**（包括 ARM Mac），不受 SDK 平台限制

---

## 5. 基本面分析 (Fundamental)

**路由前缀**: `/fundamental`

### 5.1 因子列表

| 端点 | 方法 | 说明 |
|------|------|------|
| `/factors` | GET | 返回全部 90 个基本面因子的分类清单 |

### 5.2 单股分析

| 端点 | 方法 | 说明 |
|------|------|------|
| `/analyze` | GET | 计算指定股票的全部或指定类别基本面因子 |

**因子分类**:

| 类别 | 英文名 | 指标数 | 代表因子 |
|------|--------|--------|----------|
| 盈利能力 | profitability | 9 | ROE TTM, ROA TTM, 资本回报率 TTM |
| 成长能力 | growth | 21 | 营收增速、净利润增速、EPS 增速（单季/TTM/同比/环比）|
| 营运效率 | efficiency | 15 | 资产周转率、存货周转率、毛利率、净利率 |
| 盈利质量 | earnings_quality | 8 | 应计利润占比、现金比率、经营现金流比营收 |
| 偿债安全 | safety | 14 | 资产负债率、流动比率、速动比率、产权比率 |
| 公司治理 | governance | 2 | 流通股占比、股利支付率 |
| 估值 | valuation | 12 | PE, PB, PS, PCF, 股息率, PEG |
| 股东 | shareholder | 4 | 股东数目 Z-Score、机构持仓变化、股权分散度 |
| 规模 | size | 5 | 流通市值、总市值、市值对数 |

**实现特点**:
- 基于财务报表（资产负债表、利润表、现金流量表）+ K 线数据 + 股本结构数据计算
- TTM（滚动 12 个月）、单季度、同比、环比自动处理
- 纯 pandas 实现，同样**无 SDK 平台依赖**

---

## 6. 因子分析 (Factor)

**路由前缀**: `/factor`

### 6.1 能力清单

| 端点 | 方法 | 说明 |
|------|------|------|
| `/capabilities` | GET | 列出支持的预处理方法与分析模型 |

### 6.2 单因子分析

| 端点 | 方法 | 说明 |
|------|------|------|
| `/analyze` | POST | 对指定股票列表进行单因子检验 |

**支持方法**:
- **IC 分析**: 信息系数（Pearson / Spearman）、IC 序列、ICIR
- **回归分析**: 因子收益率、显著性检验
- **分层回测**: 按因子值分组，计算各组累积收益
- **拥挤度**: 因子波动率与换手率监测

### 6.3 多因子复合

| 端点 | 方法 | 说明 |
|------|------|------|
| `/composite` | POST | 多因子加权复合评分 |

**加权方法**:
- `ic_ir`: 按 IC_IR 加权（默认）
- `equal`: 等权
- `custom`: 自定义权重

**可选处理**:
- 正交化（去除因子间共线性）

---

## 7. Tushare 协议兼容 (Tushare)

**路由前缀**: `/tushare`

兼容 Tushare Pro 请求/响应协议，现有 Tushare 客户端可零改动接入。

### 7.1 统一入口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/tushare` | POST | 统一入口，按请求体中的 `api_name` 分发到对应 handler |

**请求体**:
```json
{
  "api_name": "daily",
  "token": "<API Key>",
  "params": {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20241231"},
  "fields": ""
}
```

**响应格式**: `{"code": 0, "msg": "", "data": {"fields": [...], "items": [[...], ...]}}`

**支持的 api_name**（stock 类 8 个）:

| api_name | 说明 |
|----------|------|
| `daily` / `weekly` / `monthly` | 日 / 周 / 月 K 线 |
| `stock_basic` | 股票基础信息 |
| `trade_cal` | 交易日历 |
| `adj_factor` | 复权因子 |
| `suspend_d` | 每日停复牌 |
| `limit_list` | 涨跌停榜单 |

指数类 `index_basic` / `index_daily` 已预留 `/tushare/index/*` 命名空间，暂未实现（返回未支持错误）。

### 7.2 REST 分类路由

| 端点 | 方法 | 说明 |
|------|------|------|
| `/tushare/stock/{api_name}` | GET / POST | 上述 8 个 stock 接口的 REST 形式，如 `/tushare/stock/daily` |
| `/tushare/index/{api_name}` | GET / POST | 指数接口占位 |

### 7.3 认证

`tushare_auth` 依赖按以下顺序读取凭据（服务认证关闭时允许匿名）:

1. 请求体 `token` 字段（Tushare Pro 标准做法）
2. `X-API-Key` 请求头
3. `api_key` 查询参数

---

## 8. 实时行情 (Realtime)

**路由前缀**: `/realtime`

数据由 `amazingdata-realtime` worker 订阅写入 Redis（全市场约 5200 只 A 股 snapshot + min1 K 线，
代码表直接从 SDK 的 `EXTRA_STOCK_A_SH_SZ` 拉取），API 进程只读 Redis。

### 8.1 REST 查询

| 端点 | 方法 | 说明 |
|------|------|------|
| `/quote/{code}` | GET | 单代码最新快照 |
| `/quotes` | GET | 批量快照（`codes` 逗号分隔） |
| `/index/{code}` | GET | 指数实时快照（另有 `/index` 批量形式） |
| `/kline/{code}` | GET | 实时 min1 K 线（另有 `/kline` 批量形式） |
| `/stats` | GET | 订阅与广播运行统计 |

### 8.2 推送通道

| 端点 | 协议 | 说明 |
|------|------|------|
| `/realtime/ws` | WebSocket | 按代码订阅推送 |
| `/realtime/sse` | SSE | Server-Sent Events 推送 |

推送由 API 进程内的 realtime broadcast 服务桥接 Redis Pub/Sub 实现。

---

## 9. 历史数仓 (Historical)

**路由前缀**: `/historical`

本地 L3 数仓：Parquet 扁平布局（`data/A_share/{daily,weekly,monthly}/{code}.parquet`）+ DuckDB 查询，
由 `amazingdata-batch` worker 每日 17:10（Asia/Shanghai）同步。

### 9.1 数据查询

| 端点 | 方法 | 说明 |
|------|------|------|
| `/kline` | GET | 历史 K 线（`codes` 多代码，`period=day/week/month`） |
| `/calendar` | GET | 交易日历 |
| `/codes` | GET | 代码元数据（板块 / 上市状态过滤） |
| `/sql` | POST | DuckDB SQL 自由查询 |

### 9.2 运维管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/historical/admin/health` | GET | 数仓健康状态 |
| `/historical/admin/stats` | GET | 数仓聚合统计 |
| `/historical/admin/repair` | POST | 触发数据修复任务 |

> 设置 `HISTORICAL_ENABLED=false` 可整体关闭数仓（不挂载以上路由）。

---

## 10. 服务治理与运维

### 7.1 健康检查

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 服务整体健康状态 |
| `/login/status` | GET | AmazingData 登录状态 |
| `/login` | POST | 手动触发登录 |
| `/logout` | POST | 手动登出 |

**Health 响应字段**:
- `status`: `ok` / `degraded`
- `datasource_connected`: 数据源会话是否在线（API-only 模式下恒为 `false`，会话由 worker 持有）
- `redis_connected`: Redis 是否可达
- `auth_enabled`: 认证是否开启
- `rate_limit_enabled`: 限流是否开启

### 7.2 监控指标

| 端点 | 方法 | 说明 |
|------|------|------|
| `/metrics` | GET | Prometheus 格式指标 |

**暴露指标**:
- `adshare_request_total`: 请求计数（按 method, endpoint, status）
- `adshare_request_duration_seconds`: 请求耗时直方图
- `adshare_info`: 服务版本信息

### 7.3 限流

- 默认: 120 req/min, 10 req/sec
- 基于客户端 IP 限流（SlowAPI）
- 超出限制返回 `429 Too Many Requests`

---

## 11. 功能对照表: adshare API ↔ AmazingData SDK

| adshare 端点 | AmazingData 类 | SDK 方法 | 手册章节 |
|--------------|----------------|----------|----------|
| `/market/codes` | `BaseData` | `get_code_list()` | §3.5.2.2 |
| `/market/calendar` | `BaseData` | `get_calendar()` | §3.5.2.8 |
| `/market/kline` | `MarketData` | `query_kline()` | §3.5.4.2 |
| `/market/snapshot` | `MarketData` | `query_snapshot()` | §3.5.4.1 |
| `/market/stock/basic` | `InfoData` | `get_stock_basic()` | §3.5.2.9 |
| `/financial/statement?type=balance` | `InfoData` | `get_balance_sheet()` | §3.5.5.1 |
| `/financial/statement?type=income` | `InfoData` | `get_income()` | §3.5.5.3 |
| `/financial/statement?type=cashflow` | `InfoData` | `get_cash_flow()` | §3.5.5.2 |
| `/financial/shareholder` | `InfoData` | `get_share_holder()` | §3.5.6.1 |

> **注意**: 技术分析与基本面分析的 57+90 个指标为 adshare **原生实现**，不直接调用 SDK 的时序算子，因此跨平台可用。
> 财务接口（`/financial/*`）当前已禁用，表中对应 SDK 方法仅作恢复时的参考。

---

## 12. 缓存与性能特性

| 功能 | 说明 |
|------|------|
| Redis 实时状态 | 仅保存实时/订阅行情短期状态，默认 TTL 300s |
| 历史数据仓 | 本地 Parquet + DuckDB，由定时任务保存历史行情和元数据 |
| 查询降级 | API 进程不回源 SDK；历史仓未覆盖时返回空/部分数据，Redis 故障不影响历史查询 |
| 批量查询 | K 线支持多代码拼接，快照自动按 200 只分批 |

---

*本文档随版本迭代更新。新增功能上线后须在 3 个工作日内补充至此文档。*
