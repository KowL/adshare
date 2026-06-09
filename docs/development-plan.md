# adshare 开发计划

> 版本: 0.1.0  
> 更新日期: 2026-06-09  
> 状态: Phase 3 进行中（P0 架构收口已完成）

---

## 1. 项目愿景

成为 **中国 A 股量化数据服务的标准中间件层**：

1. **平台无关**: 任何设备、任何语言都能通过 HTTP/MCP 获取金融数据
2. **计算下沉**: 将通用技术指标、基本面因子沉淀为服务，避免各项目重复造轮子
3. **生态开放**: 通过 Skill 定义与 MCP 协议，让 AI Agent 能够自主发现与调用数据能力

---

## 2. 阶段划分

```
Phase 1: 核心数据接入     [已完成]  2025.Q4
Phase 2: 分析引擎建设     [已完成]  2026.Q1
Phase 3: 质量与性能优化   [进行中]  2026.Q2
Phase 4: 生态与扩展       [规划中]  2026.Q3
```

---

## 3. Phase 1: 核心数据接入 [已完成]

**目标**: 建立与 AmazingData SDK 的稳定连接，暴露基础行情与财务数据 API。

### 3.1 已完成工作

- [x] AmazingData SDK 单例适配器（连接池、自动重连、重试机制）
- [x] FastAPI 应用骨架（生命周期管理、中间件、路由注册）
- [x] 市场数据路由: `/market/codes`, `/kline`, `/snapshot`, `/stock/basic`, `/calendar`
- [x] 财务数据路由: `/financial/statement`, `/shareholder`
- [x] Redis 实时行情状态 + Parquet/DuckDB 历史仓体系
- [x] Docker Compose 部署（含 Redis、健康检查、日志挂载）
- [x] Prometheus Metrics 埋点
- [x] API Key 认证框架（可选开启）

### 3.2 遗留问题

- `get_calendar` / `get_code_info` 的调用方式与开发手册不完全一致，存在 SDK 版本兼容性隐患
- 测试覆盖仅限于健康检查，无数据接口集成测试

---

## 4. Phase 2: 分析引擎建设 [已完成]

**目标**: 将常用量化分析能力下沉为独立计算引擎，摆脱 SDK 平台绑定。

### 4.1 已完成工作

- [x] **技术指标引擎** (`adshare/engines/technical/`)
  - 57 个指标，分 7 大类（超买超卖、趋势、能量、成交量、均线、路径、其他）
  - 纯 pandas/numpy 实现，ARM/Mac 可直接运行
- [x] **基本面因子引擎** (`adshare/engines/fundamental/`)
  - 90 个因子，分 9 大类（盈利、成长、效率、质量、安全、治理、估值、股东、规模）
  - TTM / 单季度 / 同比 / 环比自动推导
- [x] **因子分析引擎** (`adshare/engines/factor/`)
  - IC 分析、回归检验、分层回测、多因子复合
- [x] 对应 Router 与 Pydantic Model 封装
- [x] 4 套 AI Agent Skill 定义 (`skills/adshare-*`)

### 4.2 遗留问题

- `TechnicalResponse` 在 category/all 模式下存在 Pydantic 验证 Bug（已修复）
- 涨停榜 (`/market/limit-up`) 编排曾长期停留在 Router（已迁移到 service）
- 基本面分析缺少完整的端到端测试

---

## 5. Phase 3: 质量与性能优化 [进行中]

**目标**: 修复核心缺陷，提升稳定性与吞吐量，完善测试与文档。

**时间线**: 2026-06 ~ 2026-08

### 5.0 当前进度快照（2026-06-09）

| 模块 | 状态 | 说明 |
|------|------|------|
| P0 架构收口 | ✅ 已完成 | `MarketDataService` 已承接 K 线、快照、代码表、日历、股票基础信息；`/market/kline` 与 `/historical/kline` 共用查询编排 |
| Response Mapper | ✅ 已完成 | K 线、快照、历史 K 线、历史 SQL rows 转换已移入 `adshare/services/mappers.py` |
| Service 契约测试 | ✅ 已完成 | 已覆盖 L3 命中不回源、SDK fallback、显式 warehouse 不回源、period alias、非法 source、快照未登录降级等 |
| 市场路由瘦身 | ✅ 已完成 | 常规市场数据与 `limit-up` 已收口到 service；Router 只负责 HTTP 参数与响应 |
| 分析服务化 | 🟡 部分完成 | 技术分析已迁移到 `TechnicalAnalysisService`；基本面、因子分析与 MCP 复用仍待迁移 |
| 缓存边界收口 | ✅ 已完成 | Adapter 不再缓存普通查询结果；Redis 仅用于实时/订阅行情状态；历史文件由定时任务维护 |
| Adapter 瘦身 | 🟡 部分完成 | 普通查询缓存已移除；`AmazingDataAdapter` 仍同时承担 SDK 生命周期与数据规整 |
| 当前验证 | ✅ 通过 | `pytest -q` 为 `157 passed`；`PYTHONPYCACHEPREFIX=/private/tmp/adshare_pycache python3 -m compileall -q adshare tests` 通过 |

### 5.1 🔴 P0 — 缺陷修复（2 周）

| 任务 | 负责人 | 说明 | 关联文件 |
|------|--------|------|----------|
| [x] 修复 `TechnicalResponse` 验证错误 | - | category/all 模式已由端到端测试覆盖，`indicators` 为 List | `routers/technical.py`, `tests/test_technical_e2e.py` |
| [x] 统一 SDK 调用方式 | - | `get_code_list` / `get_code_info` / `get_calendar` 已统一走 `BaseData` 实例调用；`get_calendar` 兼容有/无 `market` 参数的 SDK 版本 | `adapters/amazingdata.py`, `tests/test_amazingdata_adapter.py` |
| [x] 修复 `limit-up` name_map 不完整 | - | 已支持 code/name、索引/symbol 等常见返回布局，并抽入 `LimitUpService` | `services/limit_up.py`, `tests/test_limit_up_service.py` |
| [x] 补充 `tables` 依赖声明 | - | `tables>=3.9.0` 已声明于运行时依赖；后续只需评估 Dockerfile 是否保留重复安装 | `pyproject.toml`, `Dockerfile` |

### 5.1.1 🔴 P0 — 架构收口（2 周）

> 详见: [`docs/architecture-review.md`](architecture-review.md)

| 任务 | 负责人 | 说明 | 关联文件 |
|------|--------|------|----------|
| [x] 建立 `MarketDataService` | - | 已统一 K 线、快照、代码表、日历、股票基础信息的数据访问入口；K 线包含 L3/SDK 回退路径 | `adshare/services/market_data.py`, `routers/market.py`, `routers/historical.py` |
| [x] 统一 K 线查询路径 | - | `/market/kline` 与 `/historical/kline` 已共用 service，避免 L3/SDK fallback 逻辑重复 | `adshare/services/market_data.py`, `adshare/historical/warehouse.py` |
| [x] 建立 DataFrame -> Response mapper | - | K 线、快照、历史 K 线、历史 SQL rows mapper 已完成 | `adshare/services/mappers.py`, `models/schemas.py` |
| [x] 补充 service 契约测试 | - | 已覆盖 L3 命中不回源、SDK fallback、显式 warehouse 不回源、period alias、非法 source | `tests/test_market_data_service.py` |

### 5.2 🟠 P1 — 性能优化（3 周）

| 任务 | 目标 | 说明 |
|------|------|------|
| [x] 涨停榜服务化 | 路由瘦身 | 已由 `LimitUpService` 基于日线 K 线计算；优先读取本地历史仓，缺失时回源 AmazingData 并落盘 |
| K 线历史仓局部命中优化 | SDK 回源更少 | 对已同步年份走 Parquet/DuckDB，缺口区间才回源 SDK |
| 历史文件路径安全 | 零非法路径风险 | 保持 historical 仓文件名净化和元数据校验 |
| 引擎计算向量化 | 减少 30% CPU | 检查 indicators/factors 中循环，尽量用 pandas 原生向量化 |
| 🟡 分析服务化 | 减少重复编排 | 技术分析已迁移到 `TechnicalAnalysisService`；后续迁移基本面、因子分析，并让 MCP 直接复用 service |

### 5.3 🟡 P2 — 测试与质量（3 周）

| 任务 | 覆盖率目标 | 说明 |
|------|-----------|------|
| 市场数据集成测试 | 80% | Mock Adapter 测试 K 线、快照、代码表 |
| 技术指标端到端测试 | 100% | 每个 category 至少一个 analyze 用例 |
| 基本面因子端到端测试 | 80% | 使用预制财务 DataFrame 测试 |
| Pydantic 边界测试 | 100% | 非法日期、空代码列表、超大范围等 |
| 错误边界测试 | - | SDK 未登录、Redis 断开、空 DataFrame 返回 |

### 5.4 🟢 P3 — 文档与体验（2 周）

| 任务 | 说明 |
|------|------|
| 完善 `docs/` 文档 | 项目规范、功能手册、开发计划（本文档）|
| OpenAPI 描述补全 | 所有参数增加 `description` 与中文说明 |
| Skill 使用示例 | 为每套 Skill 提供 Python/TypeScript 调用示例 |
| 部署指南视频/图文 | 针对 x86 Docker 部署的 Troubleshooting |
| 架构评审文档维护 | 每次 Phase review 后同步更新 `docs/architecture-review.md` |

---

## 6. Phase 4: 生态与扩展 [规划中]

**目标**: 从单机服务演进为可扩展、可插件化的数据平台。

**时间线**: 2026-09 ~ 2026-12

### 6.1 功能扩展

| 模块 | 功能 | 优先级 |
|------|------|--------|
| 实时行情订阅 | SSE/WebSocket 推送 Level-1 快照 | P1 |
| 历史代码表查询 | `get_hist_code_list` 封装 | P2 |
| 复权因子 | `get_backward_factor` / `get_adj_factor` 封装 | P2 |
| 行业/指数成分 | `get_industry_constituent`, `get_index_constituent` | P2 |
| 可转债数据 | `get_kzz_*` 系列接口 | P3 |
| 期权数据 | `get_option_*` 系列接口 | P3 |
| 融资融券 | `get_margin_summary`, `get_margin_detail` | P3 |
| 龙虎榜/大宗交易 | `get_long_hu_bang`, `get_block_trading` | P3 |

### 6.2 架构演进

| 方向 | 说明 | 优先级 |
|------|------|--------|
| 多 SDK 实例负载均衡 | 支持多 AmazingData 账号并行，突破单账号连接限制 | P1 |
| Adapter 瘦身 | 拆出 `AmazingDataSession` 与原始 SDK Client，保持 adapter 只做 SDK 生命周期与原始查询 | P1 |
| 异步 SDK 调用 | 评估 AmazingData SDK 是否支持异步，减少阻塞 | P2 |
| 任务队列 | 大数据量查询（如全市场历史 K 线）转异步任务，通过回调/Webhook 返回 | P2 |
| 插件系统 | 允许用户注册自定义指标/因子（Python 脚本热加载）| P3 |
| 数据库持久化 | 可选接入 ClickHouse/TimescaleDB，用于海量历史数据仓 | P3 |

### 6.3 多协议支持

| 协议 | 说明 |
|------|------|
| gRPC | 高性能二进制传输，供内部微服务调用 |
| WebSocket | 实时行情推送 |
| MQ 适配 | Kafka / RabbitMQ 接入，支持事件驱动架构 |

---

## 7. 技术债务清单

| 编号 | 债务描述 | 严重程度 | 计划解决阶段 |
|------|----------|----------|--------------|
| TD-01 | `technical.py` Pydantic 验证 Bug | ✅ 已完成 | Phase 3 P0 |
| TD-02 | SDK 调用方式与手册不符 | ✅ 已完成 | Phase 3 P0 |
| TD-03 | `limit-up` 全市场遍历无缓存 | ✅ 已完成 | Phase 3 P1 |
| TD-04 | 测试覆盖率不足（市场、技术分析 service 测试已补强；基本面、因子与错误边界仍需补齐）| 🟡 中 | Phase 3 P2 |
| TD-05 | `tables` 依赖未声明 | ✅ 已完成 | Phase 3 P0 |
| TD-06 | 本地请求缓存 key 哈希碰撞风险 | ✅ 已完成 | Phase 3 P1 |
| TD-07 | Docker 以 root 运行 | 🟡 中 | Phase 3 P3 |
| TD-08 | CORS 全开放 | 🟡 中 | Phase 4 |
| TD-09 | 无变更日志 (CHANGELOG) | ✅ 已完成 | Phase 3 P3 |
| TD-10 | 单指标与多指标响应 key 不统一 | 🟢 低 | Phase 3 P0 |
| TD-11 | Router 承担数据源编排与响应转换（市场与技术分析已收口，基本面/因子路由待拆） | 🟡 中 | Phase 3 P1 |
| TD-12 | AmazingData Adapter 同时负责 SDK 生命周期和数据规整 | 🟡 中 | Phase 4 P1 |
| TD-13 | HTTP 与 MCP 缺少共享分析服务入口（技术分析 service 已建立，MCP/基本面/因子仍待迁移） | 🟠 中高 | Phase 3 P1 |

---

## 8. 风险评估与应对

| 风险 | 可能性 | 影响 | 应对策略 |
|------|--------|------|----------|
| AmazingData SDK 升级导致接口不兼容 | 中 | 高 | 建立 SDK 版本锁定机制；升级前在 staging 环境全量回归测试 |
| SDK 服务端限流/封禁 | 中 | 高 | 多账号负载均衡；增加请求间隔与 jitter；优先使用历史仓减少回源 |
| Docker x86 模拟性能不足 | 高（ARM Mac）| 中 | 明确仅支持 x86 服务器生产部署；ARM 仅用于代码评审 |
| Redis 单点故障 | 低 | 中 | 短期内日志告警 + 手动恢复；长期评估 Redis Sentinel |
| 全市场快照查询拖垮服务 | 中 | 高 | 增加并发限制、超时保护、客户端缓存或实时订阅聚合 |

---

## 9. 里程碑与检查点

| 日期 | 里程碑 | 验收标准 |
|------|--------|----------|
| 2026-06-22 | Phase 3 P0 完成 | P0 缺陷修复完成，CI 通过 |
| 2026-07-13 | Phase 3 P1 完成 | limit-up 服务化完成，历史仓优先查询稳定 |
| 2026-08-03 | Phase 3 P2 完成 | 集成测试覆盖率 > 80%，无已知 P0/P1 Bug |
| 2026-08-17 | Phase 3 结束 | 文档齐全，生产环境稳定运行 2 周无事故 |
| 2026-09-01 | Phase 4 启动 | 完成实时行情订阅技术方案评审 |
| 2026-12-01 | 2026 年终目标 | 支持实时推送 + 2 个新增数据模块（行业/指数）|

---

## 10. 贡献指南

### 10.1 提交规范

- 分支模型: GitHub Flow (main + feature/xxx)
- Commit message 前缀:
  - `feat:` 新功能
  - `fix:` Bug 修复
  - `perf:` 性能优化
  - `test:` 测试补充
  - `docs:` 文档更新
  - `refactor:` 代码重构

### 10.2 PR 检查清单

- [ ] 代码通过 `ruff check` 与 `ruff format`
- [ ] `mypy` 无类型错误
- [ ] 新增代码有对应测试
- [ ] 手动测试 `docker compose up` 能正常启动
- [ ] 如需，同步更新 `docs/` 与 `skills/`

---

*本计划每两周 review 一次，根据实际进展与需求变化动态调整优先级。*
