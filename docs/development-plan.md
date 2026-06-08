# adshare 开发计划

> 版本: 0.1.0  
> 更新日期: 2026-06-08  
> 状态: Phase 3 进行中

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
- [x] Redis L1 + Parquet L2 双层缓存体系
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

- `TechnicalResponse` 在 category/all 模式下存在 Pydantic 验证 Bug（500 错误）
- 涨停榜 (`/market/limit-up`) 性能差，无缓存，仅遍历全市场
- 基本面分析缺少完整的端到端测试

---

## 5. Phase 3: 质量与性能优化 [进行中]

**目标**: 修复核心缺陷，提升稳定性与吞吐量，完善测试与文档。

**时间线**: 2026-06 ~ 2026-08

### 5.1 🔴 P0 — 缺陷修复（2 周）

| 任务 | 负责人 | 说明 | 关联文件 |
|------|--------|------|----------|
| 修复 `TechnicalResponse` 验证错误 | - | category/all 模式下 `indicators` 应为 List | `routers/technical.py` |
| 统一 SDK 调用方式 | - | `get_calendar` / `get_code_info` 改为 `BaseData` 实例调用 | `adapters/amazingdata.py` |
| 修复 `limit-up` name_map 不完整 | - | 仅前 500 只股票有 name，后续为空 | `routers/market.py` |
| 补充 `tables` 依赖声明 | - | 在 `pyproject.toml` 中声明或移除 | `pyproject.toml`, `Dockerfile` |

### 5.2 🟠 P1 — 性能优化（3 周）

| 任务 | 目标 | 说明 |
|------|------|------|
| 涨停榜缓存 | 响应时间 < 2s | Redis 缓存全市场快照聚合结果，TTL 300s |
| K 线批量缓存优化 | 缓存命中率 > 80% | 按日期范围分片缓存，支持局部命中 |
| 本地缓存 key 安全 | 零碰撞风险 | 哈希前加盐、文件名非法字符过滤 |
| 引擎计算向量化 | 减少 30% CPU | 检查 indicators/factors 中循环，尽量用 pandas 原生向量化 |

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
| TD-01 | `technical.py` Pydantic 验证 Bug | 🔴 高 | Phase 3 P0 |
| TD-02 | SDK 调用方式与手册不符 | 🟠 中高 | Phase 3 P0 |
| TD-03 | `limit-up` 全市场遍历无缓存 | 🟠 中高 | Phase 3 P1 |
| TD-04 | 测试覆盖率不足（无集成测试）| 🟠 中高 | Phase 3 P2 |
| TD-05 | `tables` 依赖未声明 | 🟡 中 | Phase 3 P0 |
| TD-06 | 本地缓存 key 哈希碰撞风险 | 🟡 中 | Phase 3 P1 |
| TD-07 | Docker 以 root 运行 | 🟡 中 | Phase 3 P3 |
| TD-08 | CORS 全开放 | 🟡 中 | Phase 4 |
| TD-09 | 无变更日志 (CHANGELOG) | 🟢 低 | Phase 3 P3 |
| TD-10 | 单指标与多指标响应 key 不统一 | 🟢 低 | Phase 3 P0 |

---

## 8. 风险评估与应对

| 风险 | 可能性 | 影响 | 应对策略 |
|------|--------|------|----------|
| AmazingData SDK 升级导致接口不兼容 | 中 | 高 | 建立 SDK 版本锁定机制；升级前在 staging 环境全量回归测试 |
| SDK 服务端限流/封禁 | 中 | 高 | 多账号负载均衡；增加请求间隔与 jitter；缓存最大化 |
| Docker x86 模拟性能不足 | 高（ARM Mac）| 中 | 明确仅支持 x86 服务器生产部署；ARM 仅用于代码评审 |
| Redis 单点故障 | 低 | 中 | 短期内日志告警 + 手动恢复；长期评估 Redis Sentinel |
| 全市场快照查询拖垮服务 | 中 | 高 | 增加并发限制、超时保护、结果缓存 |

---

## 9. 里程碑与检查点

| 日期 | 里程碑 | 验收标准 |
|------|--------|----------|
| 2026-06-22 | Phase 3 P0 完成 | TD-01/02/05 修复，CI 通过 |
| 2026-07-13 | Phase 3 P1 完成 | limit-up 响应 < 2s，缓存命中率 > 80% |
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
