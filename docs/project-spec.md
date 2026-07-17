# adshare 项目规范

> 版本: 0.1.0  
> 更新日期: 2026-06-08  
> 适用范围: adshare 全部子目录及贡献者

---

## 1. 项目概述与目标

**adshare** 是 AmazingData 金融数据服务的统一中间件层，目标是将银河证券星耀数智的底层 SDK 能力封装为标准化、可共享、可观测的 HTTP API（含 WebSocket / SSE 实时推送），供多项目、多 Agent 协同调用。

核心设计原则：

- **单一数据源**: 多客户端共享同一个 AmazingData 登录会话与连接池
- **平台解耦**: 下游可在任意平台（ARM Mac、云函数、浏览器）通过 HTTP 调用，不受 SDK x86 限制
- **缓存边界清晰**: Redis 仅保存实时/订阅行情短期状态；本地 Parquet 仅由定时任务维护历史行情与元数据
- **可观测性**: Prometheus Metrics + 结构化日志，全链路可监控

---

## 2. 技术栈规范

| 层级 | 选型 | 版本约束 | 说明 |
|------|------|----------|------|
| 运行时 | Python | >=3.11 | 充分利用 `typing`、`asyncio`、`match` 等新特性 |
| Web 框架 | FastAPI | >=0.115 | 自动 OpenAPI 生成、Pydantic v2 原生支持 |
| 数据验证 | Pydantic | >=2.9 | 所有请求/响应必须定义 BaseModel |
| 实时状态 | Redis | 7.x | 仅用于实时/订阅行情短期状态与限流计数 |
| 本地历史仓 | Parquet (pyarrow) | >=18 | 定时任务保存历史行情与元数据 |
| 监控 | Prometheus Client | >=0.21 | `/metrics` 暴露标准指标 |
| 容器 | Docker + Compose | - | 必须指定 `platform: linux/amd64` |
| 日志 | structlog | >=24.4 | JSON 结构化输出，支持日志轮转 |

**禁止引入的依赖**（除非经过架构评审）：
- `SQLAlchemy` / 任何 ORM（项目无关系型数据库）
- `flask` / `django`（与 FastAPI 冲突）
- `pytables` / `h5py`（HDF5 系统库已由 `adshare/Dockerfile`（API 镜像）与 `amazingdata/base.Dockerfile`（worker 基础镜像）安装，评估后若未使用应移除）

---

## 3. 代码规范

### 3.1 Python 风格

- 使用 **Ruff** 作为 linter 与 formatter，`line-length = 120`
- 目标版本 `target-version = "py311"`
- 必须开启的规则: `E, F, I, N, W, UP, B, C4, SIM`
- 忽略 `E501`（由 formatter 自动处理换行）

### 3.2 类型注解

- **强制要求**: 所有函数参数、返回值必须带类型注解（`mypy --disallow-untyped-defs`）
- 使用 `typing` 模块的泛型，避免裸 `dict`、`list`
- 示例:
  ```python
  def get_kline(
      codes: str,
      begin_date: int,
      end_date: int,
      period: str = "day",
  ) -> pd.DataFrame:
      ...
  ```

### 3.3 错误处理

- **绝不捕获裸 `Exception` 后静默吞掉**。必须记录日志或重新抛出自定义 HTTPException
- SDK 调用统一使用 `_with_retry` 装饰器（最多 3 次，指数退避）
- 对外接口返回的 HTTP 状态码规范:
  - `200` 成功
  - `400` 参数校验失败（Pydantic ValidationError）
  - `401` API Key 缺失
  - `403` API Key 错误
  - `404` 资源不存在（如股票代码无数据）
  - `500` 服务端内部错误（SDK 异常、计算错误）
  - `503` AmazingData 连接中断；实时订阅接口可在 Redis 不可用时返回降级状态

### 3.4 日志规范

- 统一使用 `adshare.core.logging.get_logger(__name__)` 获取 logger
- 日志级别规范:
  - `DEBUG`: 详细的中间计算值、历史仓命中/SDK 回源、实时 Redis miss
  - `INFO`: 服务启动/停止、SDK 登录/登出、请求完成
  - `WARNING`: 降级处理（如 SDK 未登录返回空数据）、重试警告
  - `ERROR`: 接口调用失败、Pydantic 验证失败、未捕获异常
- **禁止在日志中打印密码、API Key、Token**

---

## 4. 目录结构规范

```
adshare/                 # FastAPI 包（API 进程，不直接依赖 AmazingData SDK）
├── main.py              # FastAPI 入口，仅做组装，不写业务逻辑
├── dependencies.py      # FastAPI 依赖注入（service / warehouse 单例）
├── core/                # 基础设施层（配置、缓存、日志、限流、认证、指标）
│   ├── config.py        # Pydantic Settings，环境变量统一管理
│   ├── cache.py         # CacheManager: Redis real-time market state only
│   ├── logging.py       # structlog 配置
│   ├── ratelimit.py     # SlowAPI / 自定义限流
│   ├── auth.py          # API Key 认证中间件
│   ├── exceptions.py    # 领域异常与 HTTP 状态映射
│   ├── realtime_keys.py # 实时数据 Redis key 规范
│   └── metrics.py       # Prometheus 指标定义
├── adapters/            # 空壳保留目录（SDK 适配已迁至 amazingdata 包）
├── clients/             # 出站客户端封装（tushare_client 等）
├── engines/             # 纯计算引擎（无外部 I/O）
│   ├── technical/       # 57 个技术指标（纯 pandas/numpy）
│   ├── fundamental/     # 90 个基本面因子（纯 pandas/numpy）
│   └── factor/          # 因子分析（IC、分层、复合）
├── historical/          # L3 历史数仓（Parquet + DuckDB）
│   ├── warehouse.py     # 仓库读写与 DuckDB 视图
│   └── admin.py         # /historical/admin 运维路由
├── routers/             # API 路由层
│   ├── health.py        # 健康检查、登录状态、手动登入/登出
│   ├── market.py        # 行情数据: codes, kline, snapshot, stock/basic, limit-up
│   ├── stock_data.py    # Pro 风格股票数据: /daily, /stock_basic, /pro_bar 等
│   ├── realtime.py      # 实时行情: REST + WebSocket(/realtime/ws) + SSE(/realtime/sse)
│   ├── historical.py    # 历史数仓查询: kline, calendar, codes, sql
│   ├── tushare/         # Tushare Pro 协议兼容（统一入口 + stock/index 分类路由）
│   ├── financial.py     # 财务数据（已禁用，直接返回 503）
│   ├── technical.py     # 技术分析: analyze, indicators 列表
│   ├── fundamental.py   # 基本面分析: analyze, factors 列表
│   └── factor.py        # 因子分析: capabilities, analyze, composite
├── models/              # Pydantic Schema
│   └── schemas.py       # 所有 Request/Response Model 集中定义
├── services/            # 业务服务层（market_data、realtime_broadcast、limit_up 等）
└── __init__.py

amazingdata/             # SDK worker 包（唯一依赖 AmazingData SDK，linux/amd64）
├── adapters/            # DataSourceAdapter: SDK 单例封装、登录与重试
├── realtime.py          # 盘中实时订阅入口（写 Redis + Pub/Sub）
├── batch.py             # 盘后同步入口（APScheduler → L3 warehouse）
├── config.py            # WorkerSettings（worker 侧环境变量）
├── wheels/              # SDK wheel 包（构建 worker 镜像用）
├── base.Dockerfile      # worker 基础镜像（SDK + 系统依赖）
├── realtime.Dockerfile  # 盘中实时订阅镜像
├── batch.Dockerfile     # 盘后同步镜像
├── docker-compose.realtime.yml
├── docker-compose.batch.yml
├── realtime.env.example # 实时 worker 配置模板（独立 TGW 账号）
└── batch.env.example    # 盘后 worker 配置模板（独立 TGW 账号）

tests/                   # 测试目录，目录结构与 adshare/ 镜像
├── conftest.py
├── test_api.py
├── test_market.py
├── test_tushare.py
├── test_realtime_push.py
├── test_historical.py
└── ...

skills/                  # AI Agent Skill 定义（供外部 Agent 读取）
├── adshare-api/
├── adshare-technical/
├── adshare-fundamental/
└── adshare-factor/

docs/                    # 项目文档（本文档所在目录）
```

所有配置均通过环境变量注入（无独立配置文件）。API 与 worker 配置已拆分:
- `adshare/.env.example` — API 服务（Redis / L3 / auth / rate limit）
- `amazingdata/realtime.env.example` — 盘中实时订阅 worker（SDK 登录 / Redis 写入）
- `amazingdata/batch.env.example` — 盘后同步 worker（SDK 登录 / 同步调度 / 维护任务）

两个 worker 使用各自独立的 TGW 账号，可同时运行。

**分层约束**:
- `routers` 通过 `dependencies` 注入并调用 `services`、`engines`、`core`
- `engines` 只能依赖 `pandas/numpy/scipy`，**严禁**导入 `AmazingData`、Redis、FastAPI
- AmazingData SDK 访问隔离在 `amazingdata` 包（worker 进程），API 进程不直接依赖 SDK
- `models` 可被任何层导入

---

## 5. API 设计规范

### 5.1 URL 与版本

- 当前无 URL 版本前缀（v0 阶段），稳定后迁移至 `/v1/...`
- 资源命名使用名词复数或集合名，如 `/market/codes`, `/financial/statement`
- 动作使用 HTTP Method 表达:
  - `GET` 查询（幂等）
  - `POST` 创建/复合计算（如 `/factor/composite`）

### 5.2 查询参数规范

- 日期统一使用 `int`，格式 `YYYYMMDD`，如 `20240608`
- 代码列表使用逗号分隔的 `str`，如 `codes=000001.SZ,600000.SH`
- 枚举值使用小写 snake_case，如 `period=day`, `statement_type=balance`

### 5.3 响应体规范

所有成功响应继承自 `BaseResponse`:

```json
{
  "success": true,
  "message": null,
  "cached": false,
  "cached_at": null,
  "count": 100,
  "data": []
}
```

错误响应:

```json
{
  "success": false,
  "error_type": "sdk_error",
  "message": "Not logged in to AmazingData",
  "suggestion": "Check /login/status or restart service"
}
```

### 5.4 Pydantic Model 规范

- 每个 Router 的 `response_model` 必须显式声明
- 字段使用 `Field(description=...)` 添加中文/英文说明
- 可选字段使用 `Optional[T] = None`，禁止隐式忽略缺失字段
- 日期字段若涉及时间序列，返回 `int` (YYYYMMDD) 或 `str` (ISO 8601)，禁止返回 `datetime` 对象给前端

---

## 6. 缓存与历史存储规范

### 6.1 职责边界

| 存储 | 保存内容 | 说明 |
|------|----------|------|
| Redis | 实时/订阅行情短期状态 | TTL 默认 300s；不保存 K 线、财务、代码表等请求结果 |
| Historical Parquet | 历史 K 线、交易日历、代码表、元数据 | 由每日定时任务写入；通过 DuckDB 查询 |
| SDK | 未同步或实时查询的回源数据 | 不在 adapter 层做通用缓存 |

### 6.2 Redis Key 规范

- 格式: `adshare:{data_type}:{params...}`
- 超过 200 字符自动 SHA-256 哈希取前 16 位，前缀改为 `adshare:hash:`
- 涉及股票代码列表时，代码按字母排序后拼接，保证幂等性

### 6.3 禁止事项

- 禁止将 K 线、财务报表、股东数据、代码表等普通查询结果写入 Redis。
- 禁止通过 `CacheManager` 写本地 Parquet。历史文件只能由 `adshare.historical` 定时同步与仓库模块维护。
- Redis 不可用时，实时行情缓存降级为 miss，不应影响历史仓或 SDK 查询路径。

---

## 7. 配置管理规范

### 7.1 配置来源优先级

1. 环境变量（最高优先级，生产环境唯一来源）
2. `.env` 文件（本地开发，参考 `adshare/.env.example` 与 `amazingdata/realtime.env.example` / `amazingdata/batch.env.example`）
3. `adshare.core.config.Settings` 中的字段默认值

### 7.2 敏感信息清单

以下信息**必须通过环境变量注入**，严禁硬编码:

- `AD_USERNAME` / `AD_PASSWORD` — AmazingData 账号
- `ADSHARE_API_KEY` — 服务认证密钥
- `REDIS_PASSWORD` — Redis 密码（如有）

### 7.3 环境变量命名

- 全部大写，单词间下划线分隔
- 带前缀区分来源: `AD_` (AmazingData), `ADSHARE_` (本服务), `REDIS_`, `CACHE_`, `RATE_LIMIT_`

---

## 8. 测试规范

### 8.1 测试分层

| 类型 | 覆盖率目标 | 工具 | 说明 |
|------|-----------|------|------|
| 单元测试 | engines 100% | pytest | 纯函数输入输出断言 |
| 集成测试 | routers 80%+ | pytest + TestClient | Mock Adapter 或连接测试容器 |
| 契约测试 | schemas 100% | pydantic | 边界值、非法格式 |

### 8.2 Mock 规范

- 测试 `routers` 时，必须 Mock `get_adapter()` 返回 FakeAdapter
- FakeAdapter 返回预制的 `pd.DataFrame`，不依赖真实 SDK
- 测试 `engines` 时，直接传入 `pd.DataFrame` / `pd.Series`，无需 Mock

### 8.3 测试命名

```python
class TestMarket:
    def test_get_code_list_success(self, client): ...
    def test_get_code_list_empty_response(self, client): ...
    def test_get_kline_invalid_date_format(self, client): ...
```

---

## 9. Docker 与部署规范

### 9.1 镜像构建

- 基础镜像: `python:3.11-slim`
- 必须显式安装系统依赖: `gcc`, `libhdf5-dev`, `curl`
- pip 使用阿里云镜像加速（国内环境）
- **必须指定 `platform: linux/amd64`**（AmazingData SDK 限制）

### 9.2 容器运行约束

- 服务端口: `8000`
- 健康检查: `curl -f http://localhost:8000/health`
- 日志目录 `/app/logs` 与历史仓目录 `/app/data` 必须挂载为 Volume
- 建议以非 root 用户运行（待实施）

### 9.3 部署检查清单

- [ ] 目标服务器架构为 x86_64（`uname -m`）
- [ ] `adshare/.env`、`amazingdata/realtime.env`、`amazingdata/batch.env` 已配置，权限为 `600`
- [ ] Docker Compose 版本 >= 2.x
- [ ] 防火墙开放 8000（API）与 6379（Redis，如外部访问）

---

## 10. 版本管理与发布

- 版本号遵循 [SemVer](https://semver.org/lang/zh-CN/): `MAJOR.MINOR.PATCH`
- 当前版本定义于:
  - `pyproject.toml` `[project] version`
  - `ADSHARE_APP_VERSION` 环境变量（部署时覆盖）
  - Docker 镜像 tag（建议）
- 变更日志: 在 `docs/CHANGELOG.md` 中维护

---

## 11. 文档规范

- 所有新增 API 必须在 `docs/` 或代码 `docstring` 中同步更新
- Skill 定义（`skills/*/SKILL.md`）变更后，必须验证下游 Agent 能否正常调用
- 与 AmazingData SDK 相关的接口，需在文档中标注对应开发手册章节

---

*本文档由项目维护者持续更新。如有冲突，以最新版本为准。*
