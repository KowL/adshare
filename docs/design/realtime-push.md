# 实时行情推送设计文档

> 版本: 0.1.0
> 日期: 2026-06-11
> 状态: 设计评审中
> 关联: Phase 4 P1 — WebSocket/SSE 实时行情推送

---

## 1. 背景与问题

### 1.1 当前架构

adshare 已形成双服务架构：

- **API 服务** (`adshare/`): FastAPI，纯 Python，处理 HTTP/WebSocket 请求，读取 Redis/L3 仓库
- **Worker 服务** (`amazingdata/`): 依赖 AmazingData SDK，负责实时订阅和定时同步

实时数据链路：

```
AmazingData SDK ──► Worker ──► Redis ──► API REST endpoints (/realtime/*)
                              │
                              └── WebSocket broadcast_loop (Worker 进程中运行)
```

### 1.2 核心问题

**WebSocket 广播在错误的进程中运行。**

当前 `RealtimeSubscriber`（位于 `adshare/services/realtime.py`）被 Worker 和 API 共同导入，但 Worker 是唯一调用 `initialize()` 和 `broadcast_loop()` 的一方：

| 组件 | 所在进程 | 职责 | 问题 |
|------|---------|------|------|
| `@router.websocket("/ws")` | API 服务 | 接受客户端连接、管理订阅 | ✅ 正常工作 |
| `WSConnectionManager` | API 服务 | 存储 WebSocket 连接和订阅关系 | ✅ 但 broadcast_loop 不在此进程 |
| `WSConnectionManager` | Worker 服务 | 独立的空实例 | ❌ 没有客户端连接 |
| `broadcast_loop()` | Worker 服务 | 从队列取消息推送到 WebSocket | ❌ 永远找不到客户端 |

**结果**: 客户端连接 WebSocket 后，subscribe 成功，但永远收不到推送。

### 1.3 根因

`RealtimeSubscriber` 同时承担两个职责：
1. **SDK 订阅者**（Worker 端）— 连接 AmazingData，接收 tick 数据
2. **WebSocket 广播器**（API 端）— 将数据推送到客户端

Phase 3 的双服务拆分将两者物理隔离到了不同进程，但代码层面未拆分，导致广播器和连接管理器分布在不同进程。

---

## 2. 设计目标

1. **解耦**: Worker 只负责 SDK → Redis，API 只负责 Redis → 客户端
2. **低延迟**: 端到端延迟 < 100ms（SDK tick → 客户端收到）
3. **可扩展**: 支持水平扩展 API 服务实例（多个 API 实例共享同一个 Redis Pub/Sub）
4. **兼容**: 保留现有 REST API (`/realtime/quote/*`, `/realtime/kline/*`) 不变
5. **双协议**: 同时支持 WebSocket 和 SSE

---

## 3. 目标架构

### 3.1 整体数据流

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                               Client                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────────┐   │
│  │ WebSocket    │  │ SSE          │  │ REST (/realtime/quote/{code})    │   │
│  │ /realtime/ws │  │ /realtime/sse│  │ /realtime/quotes?codes=...       │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┬───────────────────┘   │
└─────────┼─────────────────┼─────────────────────────┼───────────────────────┘
          │                 │                         │
┌─────────▼─────────────────▼─────────────────────────▼───────────────────────┐
│                         API 服务 (adshare)                                   │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ RealtimeBroadcastService                                            │    │
│  │  ┌─────────────────────┐  ┌──────────────────────────────────────┐ │    │
│  │  │ WSConnectionManager │  │ Redis Pub/Sub Listener               │ │    │
│  │  │ (已有，复用)        │  │ (新增)                               │ │    │
│  │  └─────────────────────┘  └──────────────────────────────────────┘ │    │
│  │                              │                                     │    │
│  │                              ▼                                     │    │
│  │  ┌───────────────────────────────────────────────────────────────┐ │    │
│  │  │ Broadcast Loop: 收到 Pub/Sub 消息 → 查订阅表 → send_json()   │ │    │
│  │  └───────────────────────────────────────────────────────────────┘ │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ SSE Manager (新增)                                                  │    │
│  │  - 维护 SSE 客户端队列                                              │    │
│  │  - 收到 Pub/Sub 消息 → 写入 queue → yield event                    │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │ Redis Pub/Sub
                                  │
┌─────────────────────────────────▼───────────────────────────────────────────┐
│                    Realtime 服务 (amazingdata.realtime)                      │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ RealtimePublisher (重构自 RealtimeSubscriber)                       │    │
│  │  ┌──────────────────┐  ┌─────────────────────────────────────────┐ │    │
│  │  │ SDK SubscribeData │  │ 回调处理:                              │ │    │
│  │  │ (已有，复用)       │  │  1. 写入 Redis (已有)                  │ │    │
│  │  └──────────────────┘  │  2. 发布到 Redis Pub/Sub (新增)        │ │    │
│  │                        └─────────────────────────────────────────┘ │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Redis Pub/Sub 设计

**Channels:**

| Channel | 数据类型 | 说明 |
|---------|---------|------|
| `adshare:realtime:quote` | 股票快照 tick | Level-1 五档行情 |
| `adshare:realtime:index` | 指数快照 tick | 大盘指数实时数据 |
| `adshare:realtime:kline:{period}` | K线 tick | min1/min5/day 等 |

**消息格式** (JSON):

```json
{
  "type": "quote",
  "code": "000001.SZ",
  "data": {
    "open": 10.5,
    "high": 10.8,
    "low": 10.3,
    "close": 10.6,
    "volume": 125000,
    "amount": 1325000.0,
    "bid1": 10.59,
    "ask1": 10.61,
    "timestamp": "2026-06-11T09:45:00.123456"
  }
}
```

```json
{
  "type": "kline",
  "code": "000001.SZ",
  "period": "min1",
  "data": {
    "open": 10.5,
    "high": 10.8,
    "low": 10.3,
    "close": 10.6,
    "volume": 125000,
    "amount": 1325000.0,
    "timestamp": "2026-06-11T09:45:00"
  }
}
```

### 3.3 为什么用 Pub/Sub 而不是轮询

| 方案 | 延迟 | Redis 压力 | 复杂度 | 选择 |
|------|------|-----------|--------|------|
| Redis Pub/Sub | < 10ms | 低（发布即推） | 中 | ✅ |
| 轮询 Redis | 50-500ms | 高（N 客户端 × M 代码） | 低 | ❌ |
| 共享内存/消息队列 | < 1ms | 无 | 高（需要额外组件） | ❌ |

Pub/Sub 是 Redis 原生能力，无需额外依赖，且天然支持多 API 实例订阅同一 channel。

---

## 4. 模块设计

### 4.1 新增/修改文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `adshare/services/realtime_broadcast.py` | 新增 | API 端广播服务（WebSocket + SSE） |
| `adshare/services/realtime_publisher.py` | 新增 | Worker 端发布服务（SDK → Redis Pub/Sub） |
| `adshare/routers/realtime.py` | 修改 | 添加 SSE endpoint，调整 WebSocket 使用 broadcast service |
| `adshare/main.py` | 修改 | lifespan 启动 broadcast 监听任务 |
| `adshare/services/realtime.py` | 修改 | 标记为 deprecated，逐步迁移到 publisher/broadcast |
| `amazingdata/main.py` | 修改 | 使用 RealtimePublisher 替代 RealtimeSubscriber |
| `adshare/core/config.py` | 修改 | 添加 Pub/Sub 相关配置 |
| `tests/test_realtime_broadcast.py` | 新增 | 广播服务单元测试 |
| `tests/test_realtime_websocket.py` | 新增 | WebSocket 集成测试 |
| `tests/test_realtime_sse.py` | 新增 | SSE 集成测试 |

### 4.2 API 端: RealtimeBroadcastService

```python
# adshare/services/realtime_broadcast.py

class RealtimeBroadcastService:
    """API 端实时广播服务。
    
    - 管理 WebSocket 连接（复用 WSConnectionManager）
    - 监听 Redis Pub/Sub，将消息推送到 WebSocket/SSE 客户端
    - 运行在 API 服务进程中
    """
    
    def __init__(self):
        self.ws_manager = WSConnectionManager()  # 已有
        self._sse_queues: Dict[str, asyncio.Queue] = {}  # SSE 客户端队列
        self._pubsub: Optional[redis.client.PubSub] = None
        self._listen_task: Optional[asyncio.Task] = None
        self.stats = {
            "ws_connections": 0,
            "ws_broadcasts": 0,
            "sse_connections": 0,
            "sse_broadcasts": 0,
            "redis_messages": 0,
            "start_time": None,
        }
    
    async def start(self) -> None:
        """启动 Redis Pub/Sub 监听。"""
        redis_client = get_cache_manager().redis
        self._pubsub = redis_client.pubsub()
        self._pubsub.subscribe(
            "adshare:realtime:quote",
            "adshare:realtime:index",
            *[f"adshare:realtime:kline:{p}" 
              for p in get_settings().realtime_kline_periods],
        )
        self._listen_task = asyncio.create_task(self._listen_loop())
        self.stats["start_time"] = datetime.now().isoformat()
    
    async def stop(self) -> None:
        """停止监听。"""
        if self._listen_task:
            self._listen_task.cancel()
        if self._pubsub:
            self._pubsub.unsubscribe()
            self._pubsub.close()
    
    async def _listen_loop(self) -> None:
        """Redis Pub/Sub 监听循环。"""
        for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            
            self.stats["redis_messages"] += 1
            
            try:
                payload = json.loads(message["data"])
                code = payload["code"]
                msg_type = payload["type"]
                
                # 1. 推送到 WebSocket 客户端
                await self._broadcast_ws(code, payload)
                
                # 2. 推送到 SSE 客户端
                await self._broadcast_sse(code, payload)
                
            except Exception as e:
                logger.error("Broadcast error: %s", e)
    
    async def _broadcast_ws(self, code: str, payload: dict) -> None:
        """推送到订阅了该 code 的 WebSocket 客户端。"""
        subscribers = self.ws_manager.get_subscribers_for_code(code)
        disconnected = []
        
        for client_id in subscribers:
            ws = self.ws_manager.get_websocket(client_id)
            if ws is None:
                disconnected.append(client_id)
                continue
            try:
                await ws.send_json(payload)
                self.stats["ws_broadcasts"] += 1
            except Exception:
                disconnected.append(client_id)
        
        for cid in disconnected:
            self.ws_manager.disconnect(cid)
    
    async def _broadcast_sse(self, code: str, payload: dict) -> None:
        """推送到订阅了该 code 的 SSE 客户端。"""
        for client_id, queue in list(self._sse_queues.items()):
            if code in queue.subscribed_codes:
                try:
                    queue.put_nowait(payload)
                    self.stats["sse_broadcasts"] += 1
                except asyncio.QueueFull:
                    pass
    
    # SSE 客户端管理
    def register_sse_client(self, client_id: str, codes: set) -> asyncio.Queue:
        """注册 SSE 客户端，返回消息队列。"""
        queue = asyncio.Queue(maxsize=1000)
        queue.subscribed_codes = codes  # monkey-patch for tracking
        self._sse_queues[client_id] = queue
        self.stats["sse_connections"] = len(self._sse_queues)
        return queue
    
    def unregister_sse_client(self, client_id: str) -> None:
        """注销 SSE 客户端。"""
        self._sse_queues.pop(client_id, None)
        self.stats["sse_connections"] = len(self._sse_queues)
```

### 4.3 Worker 端: RealtimePublisher

```python
# adshare/services/realtime_publisher.py

class RealtimePublisher:
    """Worker 端实时数据发布服务。
    
    - 连接 AmazingData SDK，订阅 tick 数据
    - 写入 Redis（供 REST API 查询）
    - 发布到 Redis Pub/Sub（供广播服务消费）
    - 运行在 Worker 服务进程中
    """
    
    def __init__(self):
        self._subscribe_data: Optional[Any] = None
        self._code_list: List[str] = []
        self._running = False
        self.stats = {
            "total_received": 0,
            "saved_to_redis": 0,
            "published": 0,
            "failed": 0,
        }
    
    def initialize(self) -> bool:
        """Login, fetch codes, setup callbacks, start subscriber thread."""
        # ... SDK login, fetch code list ...
        self._setup_callbacks()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True
    
    def _handle_snapshot(self, data: Any, period: int) -> None:
        """处理股票快照 tick。"""
        code = self._extract_code(data)
        if not code:
            return
        
        serialized = self._serialize_data(data)
        
        # 1. 写入 Redis（供 REST API 查询）
        cache = get_cache_manager()
        cache.set_realtime_market(serialized, "realtime:quote", code)
        
        # 2. 发布到 Redis Pub/Sub（新增）
        msg = json.dumps({
            "type": "quote",
            "code": code,
            "data": serialized,
            "timestamp": datetime.now().isoformat(),
        })
        cache.redis.publish("adshare:realtime:quote", msg)
        
        self.stats["published"] += 1
    
    # _handle_index_snapshot, _handle_kline 类似...
```

### 4.4 Router 调整

```python
# adshare/routers/realtime.py

from adshare.services.realtime_broadcast import get_broadcast_service

# WebSocket endpoint — 复用现有协议，改用 broadcast service
@router.websocket("/ws")
async def realtime_websocket(websocket: WebSocket):
    broadcast = get_broadcast_service()
    await websocket.accept()
    client_id = broadcast.ws_manager.connect(websocket)
    # ... 其余逻辑不变 ...

# 新增 SSE endpoint
@router.get("/sse")
async def realtime_sse(
    request: Request,
    codes: str = Query(..., description="Comma-separated stock codes"),
    types: str = Query(default="quote", description="Data types: quote,index,kline"),
):
    """Server-Sent Events for real-time quotes.
    
    Example:
      curl -N "http://localhost:8000/realtime/sse?codes=000001.SZ,600000.SH"
    """
    from fastapi.responses import EventSourceResponse
    
    broadcast = get_broadcast_service()
    client_id = f"sse_{uuid.uuid4().hex[:8]}"
    code_set = set(c.strip() for c in codes.split(",") if c.strip())
    queue = broadcast.register_sse_client(client_id, code_set)
    
    async def event_generator():
        try:
            while True:
                # 等待消息或心跳
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {
                        "event": payload["type"],
                        "data": json.dumps(payload),
                    }
                except asyncio.TimeoutError:
                    # 心跳保持连接
                    yield {"event": "heartbeat", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            broadcast.unregister_sse_client(client_id)
    
    return EventSourceResponse(event_generator())
```

### 4.5 Lifespan 调整

```python
# adshare/main.py

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 已有代码 ...
    
    # 启动实时广播监听（Redis Pub/Sub → WebSocket/SSE）
    from adshare.services.realtime_broadcast import get_broadcast_service
    broadcast = get_broadcast_service()
    await broadcast.start()
    print(f"📡 Realtime broadcast service started")
    
    yield
    
    # 关闭
    await broadcast.stop()
    print("👋 adshare api shutting down")
```

---

## 5. 接口协议

### 5.1 WebSocket 协议（已有，复用）

连接: `ws://host:port/realtime/ws`

**客户端 → 服务器:**

```json
// 订阅
{"action": "subscribe", "codes": ["000001.SZ", "600000.SH"]}

// 取消订阅
{"action": "unsubscribe"}

// 心跳
{"action": "ping"}
```

**服务器 → 客户端:**

```json
// 连接确认
{"type": "connected", "client_id": "ws_a1b2c3d4"}

// 订阅确认
{"type": "subscribed", "codes": ["000001.SZ", "600000.SH"], "count": 2}

// 心跳响应
{"type": "pong"}

// 行情推送
{"type": "quote", "code": "000001.SZ", "data": {...}, "timestamp": "..."}

// 指数推送
{"type": "index", "code": "000001.SH", "data": {...}, "timestamp": "..."}

// K线推送
{"type": "kline", "code": "000001.SZ", "period": "min1", "data": {...}, "timestamp": "..."}

// 错误
{"type": "error", "message": "codes must be a non-empty list"}
```

### 5.2 SSE 协议（新增）

连接: `GET /realtime/sse?codes=000001.SZ,600000.SH&types=quote,index`

```
event: quote
data: {"type":"quote","code":"000001.SZ","data":{...}}

event: index
data: {"type":"index","code":"000001.SH","data":{...}}

event: heartbeat
data:
```

**WebSocket vs SSE 选择:**

| 场景 | 推荐协议 | 原因 |
|------|---------|------|
| 浏览器前端 | SSE | HTTP-based，自动重连，防火墙友好 |
| 量化程序/CLI | WebSocket | 双向通信，可发送 subscribe/unsubscribe 动态调整 |
| 移动端 App | WebSocket | 更灵活的心跳和重连控制 |

---

## 6. 性能考量

### 6.1 广播效率

- **Pub/Sub**: 单条消息 publish → subscribe 延迟 < 1ms（Redis 本地）
- **WebSocket send_json**: 单客户端 < 1ms，批量 100 客户端 < 10ms
- **端到端**: SDK tick → Redis → Pub/Sub → WebSocket 客户端 < 50ms

### 6.2 连接数上限

- **Redis Pub/Sub**: 单实例可支持 10K+ subscribers，但这里 API 服务是 subscriber，不是客户端
- **WebSocket**: uvicorn 默认配置下单进程 ~1K 连接（受限于文件描述符和内存）
- **SSE**: 与 WebSocket 类似，但 HTTP 连接开销略大
- **建议**: 单 API 实例支持 500 并发 WebSocket/SSE 连接；超出时水平扩展 API 实例

### 6.3 内存预算

- 每个 WebSocket 连接: ~50KB（含缓冲区）
- 每个 SSE 连接: ~30KB
- 500 并发 WebSocket: ~25MB
- Pub/Sub 消息队列（API 端）: 默认无 backlog，实时消费

---

## 7. 错误处理

| 场景 | 处理 |
|------|------|
| Redis 连接断开 | Pub/Sub 自动重连（redis-py 内置），广播服务尝试恢复 |
| WebSocket 客户端断开 | `get_subscribers_for_code` 自动清理，send_json 异常捕获 |
| SSE 客户端断开 | `asyncio.CancelledError` 触发 cleanup，注销队列 |
| Worker 未运行 | API 端正常启动，但无数据推送；REST API 仍可查询缓存 |
| 消息序列化失败 | 记录 error log，跳过该条，不中断监听循环 |
| 消息队列满 | 丢弃最旧消息（SSE），或静默丢弃（WebSocket broadcast_queue） |

---

## 8. 测试策略

### 8.1 单元测试

```python
# tests/test_realtime_broadcast.py

class TestRealtimeBroadcastService:
    """Mock Redis Pub/Sub，验证广播逻辑。"""
    
    async def test_pubsub_message_broadcasts_to_ws_subscribers(self):
        """Pub/Sub 消息应推送到订阅了该 code 的 WebSocket。"""
    
    async def test_pubsub_message_ignored_by_unsubscribed_clients(self):
        """未订阅该 code 的客户端不应收到消息。"""
    
    async def test_ws_disconnect_removes_from_subscription(self):
        """WebSocket 断开后应从订阅表中移除。"""
    
    async def test_sse_client_receives_messages(self):
        """SSE 客户端应通过 queue 收到推送消息。"""
    
    async def test_sse_heartbeat_after_timeout(self):
        """30 秒无消息应发送心跳。"""
```

### 8.2 集成测试

```python
# tests/test_realtime_websocket.py

class TestRealtimeWebSocket:
    """使用 TestClient 测试 WebSocket endpoint。"""
    
    async def test_websocket_connect_and_subscribe(self, client):
        """连接 → 收到 connected → 发送 subscribe → 收到 subscribed。"""
    
    async def test_websocket_ping_pong(self, client):
        """发送 ping → 收到 pong。"""
    
    async def test_websocket_receives_published_message(self, client):
        """模拟 Redis publish → 客户端应收到 quote 消息。"""

# tests/test_realtime_sse.py

class TestRealtimeSSE:
    """测试 SSE endpoint。"""
    
    async def test_sse_stream_format(self, client):
        """SSE 流应符合 text/event-stream 格式。"""
    
    async def test_sse_receives_published_message(self, client):
        """模拟 Redis publish → SSE 流应包含 event。"""
```

### 8.3 压力测试（手动）

```bash
# WebSocket 并发连接测试
python scripts/loadtest_ws.py --connections 500 --codes 000001.SZ,600000.SH

# SSE 并发连接测试
python scripts/loadtest_sse.py --connections 500 --codes 000001.SZ,600000.SH
```

---

## 9. 迁移计划

### Phase 1: 新增模块（不破坏现有功能）

1. 创建 `realtime_broadcast.py` + `realtime_publisher.py`
2. Worker 使用 RealtimePublisher（替换 RealtimeSubscriber）
3. API lifespan 启动 RealtimeBroadcastService
4. 保留 `realtime.py` 作为兼容层（deprecated）

### Phase 2: Router 调整

1. WebSocket endpoint 改用 `get_broadcast_service()`
2. 新增 `/realtime/sse` endpoint
3. 验证 WebSocket 推送正常工作

### Phase 3: 清理

1. 删除 `realtime.py` 中的旧 broadcast_loop 逻辑
2. 将 RealtimeSubscriber 完全迁移到 RealtimePublisher
3. 更新文档和测试

---

## 10. 风险与应对

| 风险 | 可能性 | 影响 | 应对 |
|------|--------|------|------|
| Redis Pub/Sub 在高并发下消息丢失 | 低 | 中 | 使用消息确认机制；监控 publish/subsribe 比率 |
| 大量 WebSocket 连接导致内存耗尽 | 中 | 高 | 设置连接数上限；水平扩展 API 实例 |
| Worker 和 API 的 Redis channel 命名冲突 | 低 | 高 | 统一使用 `adshare:` 前缀；配置项管理 |
| SSE 自动重连导致消息重复 | 中 | 低 | 消息包含 timestamp/seq；客户端去重 |
| 现有 REST API 性能下降 | 低 | 中 | Pub/Sub 不影响 Redis get/set 性能；独立测试验证 |

---

## 11. 验收标准

- [ ] WebSocket 客户端 subscribe 后能实时收到行情推送（延迟 < 100ms）
- [ ] SSE 客户端能实时收到行情推送
- [ ] REST API (`/realtime/quote/*`) 继续正常工作
- [ ] 221 现有测试全部通过
- [ ] 新增测试覆盖率 > 80%（broadcast + websocket + sse）
- [ ] 支持 500 并发 WebSocket/SSE 连接
- [ ] Worker 和 API 服务可独立启停，不影响对方

---

*本文档评审通过后进入开发阶段。*
