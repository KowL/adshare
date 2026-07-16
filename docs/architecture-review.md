# adshare 代码架构评审与优化建议

> 版本: 0.2.0  
> 更新日期: 2026-06-11  
> 范围: `adshare/` 应用代码、`tests/` 测试、`docs/` 架构文档  
> 验证: `pytest -q`，221 passed

---

## 1. 当前架构判断

adshare 已经形成了清晰的服务分层：

```text
Client / Agent
    |
FastAPI Routers  (HTTP 参数、权限、状态码)
    |
Application Services  (数据编排、分析逻辑、响应映射)
    |
AmazingData Worker  /  Historical Warehouse  /  Analysis Engines
    |
Redis (实时状态)  /  Parquet + DuckDB (L3 历史仓)  /  AmazingData SDK
```

Phase 3 已完成的核心架构优化：

- **双服务架构**：API 服务 (`adshare/`) 与 Worker 服务 (`amazingdata/`) 完全分离，API 包不再依赖 SDK
- **市场数据收口**：`MarketDataService` 统一处理 K 线、快照、代码表、日历，Router 只负责 HTTP
- **分析服务化**：`TechnicalAnalysisService`、`FundamentalAnalysisService`、`FactorAnalysisService` 全部建立，MCP 与 HTTP 可复用同一入口
- **涨停榜服务化**：`LimitUpService` 基于本地 K 线计算，支持跌停榜/市场活跃度/强势股池，性能 47s→3s
- **K 线历史仓扁平化**：一股票一文件，`_metadata.json` 移至 per-period 级别
- **局部命中优化**：`get_kline` 直接查询存在的文件，不再被 `is_synced` 全有或全无阻塞
- **Response Mapper**：K 线、快照、历史 K 线、SQL rows 转换已集中

当前回归状态：**221 passed**，覆盖率 72%，核心模块 auth.py 98%、schemas.py 99%、technical/indicators.py 97%。

---

## 2. 高优先级优化建议

### 2.1 建立数据访问编排层 ✅ 已完成

**涉及文件**

- `adshare/routers/market.py`
- `adshare/routers/historical.py`
- `adshare/services/market_data.py`
- `adshare/services/mappers.py`

**状态**

Phase 3 已完全实现：

- `MarketDataService`: 统一处理 K 线、快照、代码表、日历，支持 L3 局部命中
- `ResponseMapper` (`services/mappers.py`): DataFrame → Pydantic item 转换已集中
- Router 只负责 HTTP 参数与响应，不再直接调用 adapter 或 warehouse

**遗留**

- `AnalysisDataService` 尚未建立：技术/基本面/因子分析各自通过 `MarketDataService` 获取 K 线，但财务数据入口仍待 worker 服务提供 HTTP 接口后统一

---

### 2.2 拆分 AmazingData Adapter 的职责 ✅ 已完成（第一阶段）

**涉及文件**

- `amazingdata/adapters/amazingdata.py`
- `adshare/core/cache.py`
- `adshare/historical/sync.py`

**状态**

Phase 3 已实现第一阶段拆分：

- `AmazingDataAdapter` 已从 `adshare/` 包移至 `amazingdata/` 目录
- `adshare` 包完全解耦 SDK，所有 SDK 调用通过 `MarketDataService` → warehouse 路径完成
- Worker 服务独立运行，负责 SDK 登录、实时订阅、定时同步

**遗留**

- `AmazingDataSession` 与 `AmazingDataClient` 尚未从 `AmazingDataAdapter` 中拆出
- 多账号负载均衡 session pool 待 Phase 4 实现

**建议强度**: Strong（延续到 Phase 4）

---

### 2.3 把分析入口从 Router 移到应用服务 ✅ 已完成

**涉及文件**

- `adshare/routers/technical.py`
- `adshare/routers/fundamental.py`
- `adshare/routers/factor.py`
- `adshare/services/technical_analysis.py`
- `adshare/services/fundamental_analysis.py`
- `adshare/services/factor_analysis.py`

**状态**

Phase 3 已全部实现：

- `TechnicalAnalysisService.analyze(...)`：统一技术指标计算，输入领域参数，输出 Pydantic response
- `FundamentalAnalysisService.analyze(...)`：已建立，待 worker 服务提供财务数据接口后启用
- `FactorAnalysisService.analyze(...)`：已建立，待 factor 数据表同步后启用
- 所有 Router 只负责 HTTP 参数、异常映射、状态码

**收益**

- HTTP 与 MCP 能共享同一套行为
- 分析服务可以直接单测，不需要 TestClient 才能覆盖业务流程
- 后续异步任务、批量分析、报告生成不再复制 Router 逻辑

**建议强度**: Strong

---

## 3. 中优先级优化建议

### 3.1 将 Redis 实时状态与历史仓定位写成代码契约

**问题**

项目当前不需要通用请求缓存。Redis 只应保存实时/订阅行情状态；历史 K 线、交易日历、代码表与元数据只应由 `HistoricalWarehouse` 和定时同步任务维护。若重新引入请求缓存，会和历史仓职责混淆。

**建议**

- 在 `core/cache.py` docstring 中明确 Redis 只用于实时行情状态。
- 禁止 adapter 层缓存 K 线、财务、代码表等普通查询结果。
- 在 service 层只允许 `MarketDataService` 决定 Historical Parquet 与 SDK 的查询顺序。
- 增加测试：当历史仓已同步时，`/market/kline` 不应触发 adapter 回源。

**建议强度**: Worth exploring

### 3.2 统一 DataFrame 标准化与响应映射

**问题**

当前 K 线日期转换、`code` 字段处理、空 DataFrame 行为、`pd.Timestamp` JSON 化在多个文件重复出现。历史仓库已有 `standardize_kline_df`，但市场路由和 adapter 仍有各自转换逻辑。

**建议**

- 将 K 线标准化函数提升为服务层通用工具
- 给 `KlineItem`、`SnapshotItem` 增加专门 mapper
- 所有 SDK/L3 查询输出统一经过标准化后再进入 engines 或 response

**建议强度**: Worth exploring

### 3.3 收紧运行时默认安全配置

**问题**

`main.py` 默认 CORS 全开放；`docs/development-plan.md` 已记录 Docker root 和 CORS 风险。当前适合内网开发，但如果服务被多项目共享，默认开放会扩大误用面。

**建议**

- `CORS_ALLOW_ORIGINS` 改为配置项，生产默认不使用 `*`
- `AUTH_ENABLED` 在生产 compose 示例中默认开启
- Dockerfile 增加非 root 用户运行

**建议强度**: Worth exploring

---

## 4. 开发流程建议

1. 新增功能优先从 service 层开始设计，再接 HTTP/MCP。
2. Router 测试只覆盖参数、状态码、响应模型；业务路径测试放到 service。
3. 所有外部 SDK 行为都要有 FakeAdapter 契约测试，避免真实 SDK 环境限制拖慢 CI。
4. 涉及 DataFrame 的模块先写 schema/mapper 测试，再写业务测试。
5. Phase 4 的实时推送、任务队列、多账号负载均衡都应先复用数据访问编排层，不要直接在新 router 中调用 adapter。

---

## 5. 推荐落地顺序

### Phase 3 已完成 ✅

- [x] 新建 `MarketDataService`，统一 K 线、快照、代码表、日历
- [x] `/market/kline` 与 `/historical/kline` 共用查询编排
- [x] 新建 `TechnicalAnalysisService`、`FundamentalAnalysisService`、`FactorAnalysisService`
- [x] `LimitUpService` 服务化，性能 47s→3s
- [x] K 线历史仓扁平化（一股票一文件）+ 局部命中优化
- [x] 双服务架构拆分（API / Worker）
- [x] Adapter 移至 worker 目录，adshare 包解耦 SDK
- [x] Response Mapper 集中化
- [x] 测试覆盖从 157 → 221，核心模块覆盖率 >95%

### Phase 4 规划

- **实时推送**：WebSocket/SSE Level-1 快照推送
- **任务队列**：大数据量查询（全市场历史 K 线）转异步任务
- **多账号负载均衡**：AmazingData session pool，突破单账号连接限制
- **Adapter 深度拆分**：`AmazingDataSession` + `AmazingDataClient`
- **数据库持久化**：评估 ClickHouse/TimescaleDB（L3 Parquet + DuckDB 不够用时）
- **插件系统**：用户注册自定义指标/因子

---

## 6. 本次评审结论

adshare 当前最值得投入的不是大规模重写，而是把已经存在的好分层继续“加深”：让 Router 更薄、Adapter 更窄、Service 更深。这样能保留当前测试绿色和历史仓库已有成果，同时为 Phase 4 的实时、异步、多账号和插件扩展留出稳定接口。
