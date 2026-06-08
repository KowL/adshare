# adshare 代码架构评审与优化建议

> 版本: 0.1.0  
> 更新日期: 2026-06-09  
> 范围: `adshare/` 应用代码、`tests/` 测试、`docs/` 架构文档  
> 验证: `pytest -q`，122 passed

---

## 1. 当前架构判断

adshare 已经形成了清晰的服务分层：

```text
Client / Agent
    |
FastAPI Routers
    |
AmazingData Adapter + Historical Warehouse + Analysis Engines
    |
Redis L1 / Local Parquet L2 / DuckDB Parquet L3 / AmazingData SDK
```

整体方向是正确的。`engines/technical`、`engines/fundamental`、`engines/factor` 基本保持纯计算，`historical/warehouse.py` 也已经把 DuckDB 查询、文件布局、同步状态封装成较深的模块。测试也已经覆盖了历史数据仓、市场数据、技术指标、基本面和因子分析，当前回归状态健康。

主要架构风险不在“缺少模块”，而在几个模块的接口还不够深：调用方需要知道太多实现细节，导致后续扩展行情、财务、实时推送、任务队列时容易复制编排逻辑。

---

## 2. 高优先级优化建议

### 2.1 建立数据访问编排层

**涉及文件**

- `adshare/routers/market.py`
- `adshare/routers/historical.py`
- `adshare/adapters/amazingdata.py`
- `adshare/core/cache.py`
- `adshare/historical/warehouse.py`
- `adshare/services/`

**问题**

`/market/kline` 当前在路由里直接决定 `L1 -> L3 -> L2 -> SDK` 的查询路径；`/historical/kline` 又实现了一套相近但不同的 `warehouse -> SDK` 回退；技术、基本面、因子路由也各自直接取 K 线数据。这样会让“数据源优先级、缓存命中、SDK 回源、DataFrame 标准化、响应转换”散落在多个调用点。

**建议**

在 `adshare/services/` 下建立深模块，例如：

- `MarketDataService`: 统一处理 K 线、快照、代码表、日历的读取路径
- `AnalysisDataService`: 给技术、基本面、因子引擎提供稳定的输入 DataFrame
- `ResponseMapper`: 将 DataFrame 转换为 Pydantic item，避免每个 router 手写 `iterrows`

Router 只负责 HTTP 参数、权限、状态码和 response model；数据路径由 service 决定。

**收益**

- 调整缓存顺序、增加 L3 局部命中、引入任务队列时只改一个模块
- 技术/基本面/因子分析共用同一套行情输入，减少数据格式漂移
- Router 测试变薄，service 测试成为主要契约面

**建议强度**: Strong

---

### 2.2 拆分 AmazingData Adapter 的职责

**涉及文件**

- `adshare/adapters/amazingdata.py`
- `adshare/core/cache.py`
- `adshare/historical/sync.py`

**问题**

`AmazingDataAdapter` 当前同时承担 SDK 加载、登录状态、BaseData/MarketData 初始化、重试、缓存读写、DataFrame 拼接和字段规整。这个模块的接口看起来简单，但实现变得过宽；未来多账号负载均衡、SDK 版本适配、连接限制退避都会继续压在同一个类里。

**建议**

逐步拆成三个更深的模块：

- `AmazingDataSession`: 只负责 SDK import、login/logout、BaseData/MarketData/InfoData 生命周期
- `AmazingDataClient`: 只提供原始 SDK 查询接口，做重试和错误分类
- `MarketDataService` 或 `CachedDataSource`: 负责缓存策略、L3 回退、标准化输出

短期可以先保留 `get_adapter()` 兼容旧调用，在内部委托给新模块。

**收益**

- 多 SDK 实例负载均衡可以落在 session pool，不污染业务查询
- SDK 兼容性问题可以集中用 adapter contract test 固化
- 缓存策略不再和 SDK 生命周期耦合

**建议强度**: Strong

---

### 2.3 把分析入口从 Router 移到应用服务

**涉及文件**

- `adshare/routers/technical.py`
- `adshare/routers/fundamental.py`
- `adshare/routers/factor.py`
- `adshare/engines/technical/indicators.py`
- `adshare/engines/fundamental/factors.py`
- `adshare/engines/factor/analysis.py`

**问题**

分析路由里混合了数据获取、指标函数参数推导、异常处理、结果格式化和 HTTP 响应构造。`engines` 是纯计算模块，这是好设计；但 Router 现在成了半个应用服务，导致同一能力不容易被 MCP、批任务或内部复用。

**建议**

新增：

- `TechnicalAnalysisService.analyze(...)`
- `FundamentalAnalysisService.analyze(...)`
- `FactorAnalysisService.analyze(...)`

它们输入领域参数，输出 Pydantic response 或中间 result DTO。Router 和 MCP 都调用 service。

**收益**

- HTTP 与 MCP 能共享同一套行为
- 分析服务可以直接单测，不需要 TestClient 才能覆盖业务流程
- 后续异步任务、批量分析、报告生成不再复制 Router 逻辑

**建议强度**: Strong

---

## 3. 中优先级优化建议

### 3.1 将 L2 临时缓存与 L3 历史仓库的定位写成代码契约

**问题**

文档中已明确 L2 是短期请求缓存，L3 是持久历史仓；代码中两者都使用 Parquet 和本地路径，容易在开发中混淆。`CacheManager` 的 key-based 文件缓存与 `HistoricalWarehouse` 的 schema-based 文件仓属于不同概念。

**建议**

- 在 `core/cache.py` docstring 中明确 L2 只缓存“请求结果”，不作为分析数据源
- 在 service 层只允许 `MarketDataService` 决定 L2/L3 顺序，其他模块禁止直接组合两者
- 增加测试：当 L3 已同步时，`/market/kline` 不应触发 adapter 回源

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

### 第一阶段：低风险收口

- 新建 `MarketDataService`
- 将 `/market/kline` 与 `/historical/kline` 共用同一套查询编排
- 添加 L3 命中不回源、SDK fallback、空结果三类测试

### 第二阶段：分析服务化

- 新建 `TechnicalAnalysisService`
- 再迁移 `FundamentalAnalysisService` 与 `FactorAnalysisService`
- MCP 改为调用同一 service，消除 HTTP/MCP 行为漂移

### 第三阶段：Adapter 瘦身

- 提取 `AmazingDataSession`
- 提取原始 SDK 查询 client
- 把缓存和数据源优先级从 adapter 移入 service

### 第四阶段：扩展能力

- 在稳定 service 接口上接入任务队列、WebSocket/SSE、多账号 session pool
- 再评估 ClickHouse/TimescaleDB；在 L3 Parquet + DuckDB 不够用之前不急于引入数据库

---

## 6. 本次评审结论

adshare 当前最值得投入的不是大规模重写，而是把已经存在的好分层继续“加深”：让 Router 更薄、Adapter 更窄、Service 更深。这样能保留当前测试绿色和历史仓库已有成果，同时为 Phase 4 的实时、异步、多账号和插件扩展留出稳定接口。
