# 重构 Backlog / 技术债记录

> 记录日期：2026-07-16
> 当前阶段：tushare 股票数据 API 适配 + 路由层依赖注入改造已完成

## 本轮已完成

1. **tushare 股票数据 API 适配**
   - 服务端统一入口：`POST /tushare`
   - RESTful 分类入口：`/tushare/stock/*`
   - 项目根目录 `tushare.py` 客户端适配文件（兼容已有 `import tushare as ts` 代码）
   - 旧 `/dataapi` 已废弃并返回迁移提示
2. **依赖注入改造**
   - 统一在 `adshare/dependencies.py` 提供 `get_*_dep` provider
   - 改造路由：`tushare/stock.py`、`technical.py`、`fundamental.py`、`factor.py`、`historical.py`、`stock_data.py`、`realtime.py`
   - 测试统一使用 `app.dependency_overrides` 注入 fake，移除对全局工厂的 monkeypatch
3. **回归测试**
   - 核心测试：`296 passed, 1 skipped, 2 deselected`（Python 3.9 环境）
   - 修复 `tests/conftest.py` 中 `TechnicalAnalysisService` override 判断模块错误

---

## 待重构 / 待修复

### 1. 实时推送模块测试挂起

- **位置**：`tests/test_realtime_push.py`
- **现象**：SSE 与 WebSocket 端到端用例在测试运行时会挂起，导致整轮回归超时
- **已确认挂起用例**：
  - `TestRealtimeSse::test_sse_endpoint_returns_event_stream`
  - `TestRealtimeSse::test_sse_endpoint_accepts_types_param`
- **已确认失败用例**：
  - `TestListenLoop::test_listen_loop_handles_cancelled`（期望抛出 `CancelledError`，实际被捕获后 break）
- **建议**：
  - 为 `RealtimeBroadcastService` 增加可注入的退出事件/开关，避免测试中依赖真实 asyncio sleep/listen 循环
  - 在 `TestClient` 中使用 `httpx` 异步客户端或缩短 SSE 超时
  - 将 WebSocket 测试改为直接调用 `on_*` handler 而不是走完整 ASGI 连接
- **优先级**：中

### 2. 定时任务数量断言不一致

- **位置**：`tests/test_historical.py::TestScheduler::test_init_scheduler_enabled`
- **现象**：期望 7 个定时任务，实际只有 6 个（缺少 `sync_financial`）
- **建议**：
  - 确认 `sync_financial` 是否被有意移除或重命名
  - 同步更新测试断言或调度器注册逻辑
- **优先级**：低

### 3. 认证错误码边界不一致

- **位置**：`tests/test_auth.py::TestAPIKeyAuth::test_no_server_key_raises_500`
- **现象**：未配置 `ADSHARE_API_KEY` 且 `AUTH_ENABLED=true` 时，调用 `APIKeyAuth(api_key="any-key")` 实际返回 403，但测试期望 500
- **建议**：
  - 明确设计：服务端未配置 key 时应视为未就绪（500）还是直接拒绝所有请求（403）
  - 统一实现与测试
- **优先级**：低

### 4. 测试环境与项目 Python 版本要求不一致

- **位置**：`tests/test_limit_up_service.py`
- **现象**：该测试文件使用 `pd.DataFrame | None` 等 Python 3.10+ 语法，当前 3.9 环境无法收集
- **说明**：`pyproject.toml` 已要求 `requires-python = ">=3.11"`，因此这属于**环境不匹配**，不是代码 bug
- **建议**：
  - 开发/CI 统一使用 Python 3.11 或 3.12
  - 必要时创建项目内 `.venv`（`/opt/homebrew/bin/python3.12`）并安装依赖
- **优先级**：高（基础设施）

### 5. 指数数据接口扩展

- **位置**：`adshare/routers/tushare/index.py`
- **现状**：`/tushare/index/*` 路由已预留，但尚未实现具体 handler
- **建议后续实现**：
  - `index_basic`：指数基础信息
  - `index_daily`：指数日线行情
  - 统一通过 `MarketDataService` 或新增 `IndexDataService` 获取数据
  - 客户端 `tushare.py` 无需改动即可直接使用
- **优先级**：高（业务扩展）

### 6. realtime 路由生命周期与资源清理

- **位置**：`adshare/routers/realtime.py`
- **现状**：已改为 `Depends` 注入 `CacheManager` / `RealtimeBroadcastService`，但 WebSocket/SSE 连接的生命周期、任务取消、队列清理仍需要更健壮的边界处理
- **建议**：
  - 在 `RealtimeBroadcastService.stop()` 中确保所有 client queue 被清空
  - WebSocket disconnect 时显式 unregister SSE client
  - 增加最大连接数与单客户端队列长度限制
- **优先级**：中

### 7. 统一错误码与异常边界

- **位置**：全路由层
- **现状**：部分路由对 `HistoricalWarehouse` 未启用、空数据、参数错误的返回码存在差异
- **建议**：
  - 统一使用 `adshare.core.exceptions` 中定义的异常类
  - 在 `adshare/main.py` 中集中注册错误处理中间件，避免各路由重复 `try/except`
- **优先级**：中

---

## 后续开发建议顺序

1. **基础设施**：切到 Python 3.11+ 环境，补齐 `.venv` 或 CI 镜像
2. **业务扩展**：实现 `/tushare/index/*` 指数接口
3. **稳定性**：修复 realtime 推送模块测试挂起与生命周期问题
4. **代码质量**：统一异常处理与错误码
5. **清理**：处理 scheduler / auth 两个低优先级测试断言不一致
