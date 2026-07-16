# Phase 4 架构设计文档

> 版本: 0.1.0
> 日期: 2026-06-12
> 状态: 设计评审中
> 关联: Phase 4 生态与扩展

---

## 1. 背景与目标

### 1.1 Phase 3 已完成

- API 服务与 Worker 服务分离，API 不再依赖 SDK
- `MarketDataService` 统一历史数据访问（L3 warehouse + SDK fallback）
- 涨停榜服务化、K 线历史仓扁平化、引擎向量化
- 221 测试通过，核心模块覆盖率 >95%
- WebSocket/SSE 实时推送基础架构已落地（Redis Pub/Sub 桥接）

### 1.2 Phase 4 目标

从单机服务演进为**可扩展、可插件化的数据平台**：

1. **突破单账号瓶颈**: 多 SDK 实例负载均衡
2. **Adapter 职责清晰**: 拆出 `AmazingDataSession` + `AmazingDataClient`
3. **扩展数据覆盖**: 行业/指数/可转债/期权/融资融券/龙虎榜
4. **提升吞吐**: 异步 SDK 调用、任务队列
5. **多协议支持**: gRPC、WebSocket、MQ

---

## 2. 整体架构

### 2.1 目标服务分层

```
Client / Agent / AI
    │
    ├── HTTP (FastAPI Routers)
    ├── WebSocket/SSE (Realtime)
    ├── gRPC (Internal microservices)
    └── MCP (Model Context Protocol)
    │
    ▼
Application Services  (MarketDataService / AnalysisServices / TaskService)
    │
    ▼
Data Access Layer     (HistoricalWarehouse / SDK Session Pool / Redis / MQ)
    │
    ▼
AmazingData Worker    (Multi-session pool + Sync Scheduler + Realtime Publisher)
```

### 2.2 Phase 4 实施路线

| 阶段 | 任务 | 优先级 | 状态 |
|------|------|--------|------|
| P4-1 | 多 SDK 实例负载均衡 + Adapter 深度拆分 | P1 | 设计中 |
| P4-2 | WebSocket/SSE 实时推送收尾与性能压测 | P1 | 已实现，待收尾 |
| P4-3 | 历史代码表查询 + 复权因子标准化接口 | P2 | 规划中 |
| P4-4 | 行业/指数成分数据接入 | P2 | 规划中 |
| P4-5 | 异步 SDK 调用 + 任务队列 | P2 | 规划中 |
| P4-6 | 可转债/期权/融资融券/龙虎榜 | P3 | 规划中 |
| P4-7 | 插件系统 + 数据库持久化评估 | P3 | 规划中 |

---

## 3. P4-1: 多 SDK 负载均衡 + Adapter 深度拆分

### 3.1 当前问题

当前 `AmazingDataAdapter` 是**单例 + 单连接**：

```python
class AmazingDataAdapter:
    _instance: Optional["AmazingDataAdapter"] = None
    _lock = threading.Lock()

    def __init__(self, settings=None):
        self._client = None
        self._base_data = None
        self._info_data = None
        self._market_data = None
```

**问题:**

1. **单账号连接限制**: AmazingData 服务端对单账号并发连接/请求有限制，高并发时触发 `status[-98]` 或 `exceed the max limitation`
2. **单点故障**: 一个 session 断开或限流，整个 Worker 服务不可用
3. **职责混杂**: Adapter 同时管理 SDK 生命周期（login/logout）、连接池、数据规整、重试逻辑
4. **无法水平扩展**: 单进程内只能有一个 SDK client，不能利用多账号
5. **阻塞式调用**: SDK C 扩展阻塞主线程，已出现 GIL 问题（参考 `_run_reference_sync_subprocess`）

### 3.2 设计目标

1. **多账号 session pool**: 支持配置 N 个 AmazingData 账号，Worker 内维护 pool
2. **请求级负载均衡**: 每个数据请求路由到可用 session
3. **故障隔离**: 单个 session 失败不影响其他 session
4. **Adapter 瘦身**: 生命周期归 `AmazingDataSession`，原始查询归 `AmazingDataClient`，Adapter 只做路由
5. **向后兼容**: 现有 `get_adapter()` 接口保持兼容，内部使用新 pool

---

## 4. 模块设计

### 4.1 新增/修改文件

```
amazingdata/
├── adapters/
│   ├── __init__.py
│   ├── amazingdata.py          # 保持兼容，内部委托给 pool
│   ├── amazingdata_session.py  # 单个 SDK session 生命周期
│   ├── amazingdata_client.py   # 原始 SDK 查询封装
│   └── session_pool.py         # 多 session 管理与负载均衡
├── services/
│   └── sdk_router.py           # 请求级路由（可选）
└── main.py                     # 初始化 pool，替代单 adapter

adshare/
├── core/config.py              # 新增多账号配置
```

### 4.2 AmazingDataSession

```python
# amazingdata/adapters/amazingdata_session.py

import threading
import time
from typing import Any, Dict, Optional

from adshare.core.config import Settings
from adshare.core.logging import get_logger

logger = get_logger(__name__)


class AmazingDataSession:
    """封装单个 AmazingData SDK session 的生命周期。

    - 每个 session 使用一组 (username, password) 登录
    - 管理 ad.BaseData, ad.query_api.market_data.MarketData, InfoData
    - 提供健康检查和故障恢复
    """

    def __init__(self, session_id: str, settings: Settings) -> None:
        self.session_id = session_id
        self.settings = settings

        self._client: Optional[Any] = None
        self._base_data: Optional[Any] = None
        self._info_data: Optional[Any] = None
        self._market_data: Optional[Any] = None

        self._login_info: Optional[Dict[str, Any]] = None
        self._lock = threading.RLock()
        self._last_error: Optional[Exception] = None
        self._fail_count = 0
        self._created_at = time.time()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """Login and initialize SDK objects."""
        try:
            import AmazingData as ad

            self._client = ad
            self._info_data = ad.query_api.info_data.InfoData()
            self._base_data = ad.BaseData()

            result = self._client.login(
                username=self.settings.ad_username,
                password=self.settings.ad_password,
                host=self.settings.ad_host,
                port=self.settings.ad_port,
            )
            if not result:
                logger.error("Session %s login failed", self.session_id)
                return False

            self._login_info = {"status": True, "timestamp": time.time()}
            self._ensure_market_data()
            logger.info("Session %s initialized", self.session_id)
            return True

        except Exception as e:
            logger.error("Session %s init failed: %s", self.session_id, e)
            self._last_error = e
            return False

    def _ensure_market_data(self) -> None:
        """Initialize MarketData (requires calendar)."""
        import AmazingData as ad

        if self._market_data is not None:
            return

        try:
            calendar = self._base_data.get_calendar()
        except Exception:
            calendar = []

        try:
            self._market_data = ad.query_api.market_data.MarketData(calendar=calendar)
        except Exception:
            self._market_data = ad.query_api.market_data.MarketData(calendar=[])

    def is_healthy(self) -> bool:
        """Check if session is logged in and not exceeded fail threshold."""
        if self._login_info is None:
            return False
        if self._fail_count >= 5:
            return False
        return True

    def mark_failure(self, error: Exception) -> None:
        """Record a request failure."""
        self._fail_count += 1
        self._last_error = error
        if self._fail_count >= 5:
            logger.warning("Session %s marked unhealthy after %d failures", self.session_id, self._fail_count)

    def mark_success(self) -> None:
        """Reset failure count on success."""
        self._fail_count = 0
        self._last_error = None

    def logout(self) -> None:
        """Logout and release resources."""
        with self._lock:
            self._login_info = None
            self._client = None
            self._base_data = None
            self._info_data = None
            self._market_data = None
            logger.info("Session %s logged out", self.session_id)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def client(self) -> Any:
        return self._client

    @property
    def base_data(self) -> Any:
        return self._base_data

    @property
    def info_data(self) -> Any:
        return self._info_data

    @property
    def market_data(self) -> Any:
        return self._market_data

    @property
    def is_logged_in(self) -> bool:
        return self._login_info is not None
```

### 4.3 AmazingDataClient

```python
# amazingdata/adapters/amazingdata_client.py

from typing import Any, List, Optional

import pandas as pd

from adshare.core.logging import get_logger
from amazingdata.adapters.amazingdata_session import AmazingDataSession

logger = get_logger(__name__)


class AmazingDataClient:
    """原始 SDK 查询封装，不包含生命周期管理。

    所有方法接收一个 AmazingDataSession 实例，执行具体查询。
    """

    @staticmethod
    def get_code_list(session: AmazingDataSession, security_type: str = "EXTRA_STOCK_A") -> List[str]:
        """Get code list using session's BaseData."""
        return list(session.base_data.get_code_list(security_type=security_type))

    @staticmethod
    def get_code_info(session: AmazingDataSession, security_type: str = "EXTRA_STOCK_A") -> pd.DataFrame:
        """Get code info using session's BaseData."""
        return session.base_data.get_code_info(security_type=security_type)

    @staticmethod
    def get_calendar(session: AmazingDataSession, market: str = "SH", date: Optional[int] = None) -> pd.DataFrame:
        """Get trading calendar."""
        try:
            calendar_list = session.base_data.get_calendar(market=market)
        except TypeError:
            calendar_list = session.base_data.get_calendar()

        if isinstance(calendar_list, pd.DataFrame):
            df = calendar_list
        elif isinstance(calendar_list, list):
            df = pd.DataFrame({"date": calendar_list})
        else:
            df = pd.DataFrame({"date": []})

        if date is not None and "date" in df.columns:
            df = df[df["date"] == date]
        return df

    @staticmethod
    def get_kline(
        session: AmazingDataSession,
        codes: str,
        begin_date: int,
        end_date: int,
        period: str = "day",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> pd.DataFrame:
        """Get K-line data using session's MarketData."""
        period_map = {
            "tick": 0, "min1": 10000, "min3": 10001, "min5": 10002,
            "min10": 10003, "min15": 10004, "min30": 10005, "min60": 10006,
            "min120": 10007, "day": 10008,
            "week": 10009, "month": 10010,
        }
        period_code = period_map.get(period, 10000)

        code_list = [AmazingDataClient._ensure_suffix(c) for c in codes.split(",")] if "," in codes else [AmazingDataClient._ensure_suffix(codes)]
        result_dict = session.market_data.query_kline(
            code_list=code_list,
            begin_date=int(begin_date),
            end_date=int(end_date),
            period=period_code,
        )

        dfs = []
        for code, df in result_dict.items():
            if isinstance(df, pd.DataFrame) and not df.empty:
                df = df.copy()
                df["code"] = code
                dfs.append(df)

        if dfs:
            df = pd.concat(dfs, ignore_index=True)
        else:
            df = pd.DataFrame()

        if limit is not None and not df.empty:
            df = df.iloc[offset:offset + limit]
        return df

    @staticmethod
    def _ensure_suffix(code: str) -> str:
        c = code.strip()
        if "." in c:
            return c
        if len(c) == 6 and c.isdigit():
            if c.startswith(("60", "68", "69")):
                return f"{c}.SH"
            elif c.startswith(("00", "30", "39")):
                return f"{c}.SZ"
            elif c.startswith(("8", "4", "9")):
                return f"{c}.BJ"
        return c
```

### 4.4 SessionPool

```python
# amazingdata/adapters/session_pool.py

import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar

from adshare.core.config import get_settings
from adshare.core.logging import get_logger
from amazingdata.adapters.amazingdata_session import AmazingDataSession

logger = get_logger(__name__)

T = TypeVar("T")


class SessionPool:
    """Manage multiple AmazingData SDK sessions with load balancing.

    - Round-robin by default
    - Skip unhealthy sessions
    - Retry on other sessions when one fails
    """

    def __init__(self, size: int = 1) -> None:
        self.size = max(1, size)
        self._sessions: Dict[str, AmazingDataSession] = {}
        self._lock = threading.Lock()
        self._round_robin_index = 0

        settings = get_settings()
        for i in range(self.size):
            session_id = f"sdk_{i + 1}"
            self._sessions[session_id] = AmazingDataSession(session_id, settings)

    def initialize_all(self) -> List[str]:
        """Initialize all sessions, return list of healthy session IDs."""
        healthy = []
        for sid, session in self._sessions.items():
            if session.initialize():
                healthy.append(sid)
            else:
                logger.error("Failed to initialize session %s", sid)
        return healthy

    def get_healthy_sessions(self) -> List[AmazingDataSession]:
        """Return all healthy sessions."""
        return [s for s in self._sessions.values() if s.is_healthy()]

    def pick_session(self, strategy: str = "round_robin") -> Optional[AmazingDataSession]:
        """Pick a healthy session using the given strategy."""
        healthy = self.get_healthy_sessions()
        if not healthy:
            return None

        if strategy == "random":
            return random.choice(healthy)

        if strategy == "least_failures":
            return min(healthy, key=lambda s: s._fail_count)

        # round_robin
        with self._lock:
            for _ in range(len(healthy)):
                session = healthy[self._round_robin_index % len(healthy)]
                self._round_robin_index += 1
                return session
        return healthy[0]

    def execute(
        self,
        func: Callable[[AmazingDataSession], T],
        retry_on_session_failure: bool = True,
    ) -> T:
        """Execute a query function on a session, with failover.

        Args:
            func: Function that takes a session and returns result.
            retry_on_session_failure: If True, retry on other sessions when one fails.
        """
        attempted = set()
        last_error = None

        while True:
            session = self.pick_session()
            if session is None:
                raise RuntimeError("No healthy SDK session available")
            if session.session_id in attempted:
                # Already tried all healthy sessions
                break

            attempted.add(session.session_id)
            try:
                result = func(session)
                session.mark_success()
                return result
            except Exception as e:
                last_error = e
                session.mark_failure(e)
                err_str = str(e).lower()
                is_session_error = any(
                    keyword in err_str
                    for keyword in ("status[-98]", "exceed the max limitation", "not logged in", "connection")
                )
                if not retry_on_session_failure or not is_session_error:
                    raise
                logger.warning(
                    "Session %s failed, trying another session: %s",
                    session.session_id, e
                )

        if last_error:
            raise last_error
        raise RuntimeError("No healthy SDK session available")

    def shutdown_all(self) -> None:
        """Logout and release all sessions."""
        for session in self._sessions.values():
            session.logout()
        self._sessions.clear()

    def health(self) -> Dict[str, Any]:
        """Return pool health status."""
        return {
            "total": len(self._sessions),
            "healthy": len(self.get_healthy_sessions()),
            "sessions": {
                sid: {
                    "logged_in": s.is_logged_in,
                    "fail_count": s._fail_count,
                    "healthy": s.is_healthy(),
                }
                for sid, s in self._sessions.items()
            },
        }


# Singleton
_pool: Optional[SessionPool] = None
_pool_lock = threading.Lock()


def get_session_pool(size: Optional[int] = None) -> SessionPool:
    """Get or create the global session pool."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                settings = get_settings()
                _pool = SessionPool(size=size or settings.ad_pool_size)
    return _pool
```

### 4.5 兼容层 AmazingDataAdapter

```python
# amazingdata/adapters/amazingdata.py

from typing import Any, Dict, List, Optional

import pandas as pd

from adshare.core.config import Settings, get_settings
from adshare.core.logging import get_logger
from amazingdata.adapters.amazingdata_client import AmazingDataClient
from amazingdata.adapters.session_pool import SessionPool, get_session_pool

logger = get_logger(__name__)


class AmazingDataAdapter:
    """Backward-compatible adapter facade.

    Internally delegates all queries to SessionPool.
    Existing code using get_adapter() continues to work.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._pool = get_session_pool(self.settings.ad_pool_size)

    def login(self) -> bool:
        """Initialize all sessions in pool."""
        healthy = self._pool.initialize_all()
        return len(healthy) > 0

    def ensure_login(self) -> bool:
        """Ensure at least one session is healthy."""
        healthy = self._pool.get_healthy_sessions()
        if healthy:
            return True
        return self.login()

    def logout(self) -> None:
        """Shutdown all sessions."""
        self._pool.shutdown_all()

    @property
    def is_logged_in(self) -> bool:
        return len(self._pool.get_healthy_sessions()) > 0

    @property
    def login_info(self) -> Optional[Dict[str, Any]]:
        health = self._pool.health()
        return {
            "healthy_sessions": health["healthy"],
            "total_sessions": health["total"],
        }

    # ------------------------------------------------------------------
    # Data APIs — delegate to pool + client
    # ------------------------------------------------------------------

    def get_code_list(self, security_type: str = "EXTRA_STOCK_A") -> List[str]:
        return self._pool.execute(lambda s: AmazingDataClient.get_code_list(s, security_type))

    def get_code_info(self, security_type: str = "EXTRA_STOCK_A") -> pd.DataFrame:
        return self._pool.execute(lambda s: AmazingDataClient.get_code_info(s, security_type))

    def get_calendar(self, market: str = "SH", date: Optional[int] = None) -> pd.DataFrame:
        return self._pool.execute(lambda s: AmazingDataClient.get_calendar(s, market, date))

    def get_kline(
        self,
        codes: str,
        begin_date: int,
        end_date: int,
        period: str = "day",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> pd.DataFrame:
        return self._pool.execute(
            lambda s: AmazingDataClient.get_kline(s, codes, begin_date, end_date, period, limit, offset)
        )

    # ... 其他方法类似 ...

    def health(self) -> Dict[str, Any]:
        return self._pool.health()


# Singleton accessor (backward compatible)
_adapter: Optional[AmazingDataAdapter] = None


def get_adapter() -> AmazingDataAdapter:
    """Get singleton adapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = AmazingDataAdapter()
    return _adapter
```

---

## 5. 配置设计

### 5.1 多账号配置

当前配置只有一个账号：

```python
ad_username: str = Field(default="", alias="AD_USERNAME")
ad_password: str = Field(default="", alias="AD_PASSWORD")
```

扩展为多账号：

```python
# 保留单账号兼容
ad_username: str = Field(default="", alias="AD_USERNAME")
ad_password: str = Field(default="", alias="AD_PASSWORD")

# 新增多账号池（JSON 格式环境变量）
ad_accounts: List[Dict[str, str]] = Field(default=[], alias="AD_ACCOUNTS")
# 或
ad_accounts_json: Optional[str] = Field(default=None, alias="AD_ACCOUNTS_JSON")
```

环境变量示例：

```env
# 方式1: 单账号（兼容现有）
AD_USERNAME=user1
AD_PASSWORD=pass1
AD_POOL_SIZE=3

# 方式2: 多账号
AD_ACCOUNTS_JSON=[
  {"username":"user1","password":"pass1"},
  {"username":"user2","password":"pass2"},
  {"username":"user3","password":"pass3"}
]
```

`SessionPool` 初始化时：
1. 如果 `ad_accounts_json` 存在，按账号数量创建 session
2. 否则使用单账号 `AD_USERNAME/AD_PASSWORD`，创建 `ad_pool_size` 个相同账号的 session

> 注意：多个相同账号的 session 也能提高并发吞吐（服务端连接数限制按 session 计）

### 5.2 负载均衡策略配置

```python
ad_lb_strategy: str = Field(default="round_robin", alias="AD_LB_STRATEGY")
# 可选: round_robin, random, least_failures
```

---

## 6. Worker 启动流程

```python
# amazingdata/main.py

def main() -> int:
    # ...

    # 1. Initialize session pool
    pool = get_session_pool(settings.ad_pool_size)
    healthy = pool.initialize_all()
    if not healthy:
        logger.error("No SDK sessions could be initialized, exiting")
        return 1
    logger.info("Session pool ready: %d/%d healthy", len(healthy), settings.ad_pool_size)

    # 2. L3 warehouse init
    # ...

    # 3. Realtime publisher
    # ...

    # 4. Sync scheduler
    # ...

    # 5. Main loop
    # ...

    # Shutdown
    shutdown_scheduler()
    try:
        get_session_pool().shutdown_all()
    except Exception:
        pass
```

---

## 7. 错误处理与故障转移

### 7.1 可重试错误

| 错误类型 | 关键词 | 处理 |
|----------|--------|------|
| 连接限制 | `status[-98]`, `exceed the max limitation` | 标记 session 失败，切换 session 重试 |
| 未登录 | `not logged in` | 触发该 session 重新登录，切换 session 重试 |
| 连接断开 | `connection` | 标记 session 失败，切换 session 重试 |
| 业务错误 | `invalid code`, `no data` | 不重试，直接抛出 |

### 7.2 Session 健康检查

- 连续失败 5 次 → 标记 unhealthy
- 成功一次 → 重置失败计数
- Worker 启动时初始化所有 session
- 可定期（每 60s）对 unhealthy session 尝试恢复

---

## 8. 测试策略

### 8.1 Session 单元测试

```python
def test_session_initializes_and_logs_in(monkeypatch):
    """Mock AmazingData SDK, verify session.initialize() works."""

def test_session_mark_failure_unhealthy():
    """After 5 failures, session should be unhealthy."""

def test_session_mark_success_resets_failures():
    """Success should reset fail count."""
```

### 8.2 Pool 单元测试

```python
def test_pool_execute_round_robin():
    """Multiple calls should distribute across sessions."""

def test_pool_execute_failover():
    """When one session fails with connection error, retry on another."""

def test_pool_execute_no_healthy_raises():
    """When all sessions unhealthy, execute should raise."""

def test_pool_health_returns_status():
    """health() should report total/healthy sessions."""
```

### 8.3 Adapter 兼容测试

```python
def test_adapter_get_code_list_delegates_to_pool(monkeypatch):
    """Existing get_adapter().get_code_list() should use pool."""
```

### 8.4 Worker 集成测试

```python
def test_worker_initializes_pool_on_start(monkeypatch):
    """Worker main should call pool.initialize_all()."""
```

---

## 9. 实施步骤

| 步骤 | 任务 | 说明 | 工作量 |
|------|------|------|--------|
| 1 | 创建 `amazingdata_session.py` | 单个 session 生命周期 | 0.5 天 |
| 2 | 创建 `amazingdata_client.py` | 原始查询封装（从 adapter 拆出） | 1 天 |
| 3 | 创建 `session_pool.py` | 多 session 管理与负载均衡 | 1 天 |
| 4 | 重构 `amazingdata.py` | 兼容 facade，委托给 pool | 0.5 天 |
| 5 | 扩展 `config.py` | 多账号配置 | 0.5 天 |
| 6 | 更新 `worker/main.py` | 使用 pool 初始化 | 0.5 天 |
| 7 | 编写测试 | Session/Pool/Adapter/Worker 测试 | 1 天 |
| 8 | 文档更新 | 部署指南、CHANGELOG | 0.5 天 |

**总计: 5.5 天**

---

## 10. 风险与应对

| 风险 | 可能性 | 影响 | 应对 |
|------|--------|------|------|
| 多 session 登录被服务端限制 | 中 | 高 | 增加登录间隔 jitter；相同账号 session 数限制为 3-5 |
| SDK 全局状态导致 session 不隔离 | 中 | 高 | 充分测试；必要时进程隔离（一个 worker 一个 session） |
| Session 切换引入延迟 | 低 | 中 | 使用连接池预热； unhealthy session 定期恢复 |
| 现有 `_with_retry` 行为变化 | 低 | 中 | 保持兼容 facade，内部使用 pool.execute |

---

## 11. 验收标准

- [ ] `SessionPool` 支持 3+ session，并提供 round-robin/random/least_failures 策略
- [ ] 单个 session 失败时自动切换到其他 session
- [ ] 现有 `get_adapter().get_kline()` 等接口行为不变
- [ ] Worker 启动时初始化 pool，shutdown 时释放所有 session
- [ ] 新增测试覆盖率 >80%
- [ ] 全量测试 `pytest -q` 通过

---

*本设计评审通过后进入开发阶段。*
