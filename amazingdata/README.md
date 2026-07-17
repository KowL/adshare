# `amazingdata/` — AmazingData SDK subsystem

把原来混在一起的 `amazingdata_worker/` 拆成 vendor wheels + 共享 SDK 适配层 + 两个独立运行模式（盘中 / 盘后）。

## 为什么拆成两个独立入口

TGW 单连接账户约束：同一账号在同一时刻只能持有一个 SDK session。原来的 `amazingdata_worker` 进程里用 `REALTIME_ENABLED` + `SYNC_SCHEDULE_ENABLED` 两个开关在同进程混跑实时订阅和定时同步，容易人为切换错。新结构：

| 入口 | 镜像 | 进程职责 |
|------|------|---------|
| `amazingdata.realtime` | `amazingdata-realtime` | 盘中：订阅 snapshot / index / kline → Redis + Pub/Sub |
| `amazingdata.batch` | `amazingdata-batch` | 盘后：APScheduler 驱动 K线/meta/参考数据 → L3 warehouse (Parquet + DuckDB) |

两个进程物理隔离。单账号时代需要外部调度切换容器来互斥；现在 realtime / batch 各用各的 TGW 账号（见下文「配置」），可同时运行。

## 目录结构

```
amazingdata/
├── README.md                 ← 本文件
├── __init__.py               ← Python 包入口
├── adapters/                 ← 两个模式共用的 SDK 适配层
│   ├── base.py               ← DataSourceAdapter / SubscriptionSource Protocol
│   └── amazingdata.py        ← AmazingDataAdapter（534 行，封装 SDK 调用）
├── wheels/                   ← vendor whl（git 不追踪，按需放置）
│   ├── AmazingData-1.1.8-cp311-none-any.whl   ← 当前使用版本
│   └── tgw-1.0.8.7-py3-none-any.whl
├── realtime.py               ← 盘中模式入口（458 行，单文件）
├── batch.py                  ← 盘后模式入口（1250 行，单文件，含全部 sync 任务 + scheduler）
├── base.Dockerfile           ← SDK + C 扩展编译层（apt + whl + numba/scipy/statsmodels）
├── realtime.Dockerfile       ← FROM adshare-base，CMD python -m amazingdata.realtime
├── batch.Dockerfile          ← FROM adshare-base，CMD python -m amazingdata.batch
├── docker-compose.realtime.yml  ← 服务 amazingdata-realtime
├── docker-compose.batch.yml     ← 服务 amazingdata-batch
├── realtime.env.example      ← realtime 配置模板（拷成 realtime.env 填账号）
└── batch.env.example         ← batch 配置模板（拷成 batch.env 填账号）
```

## 构建与启动

### 配置（一次性）

每个服务一份 env 文件，gitignored，各自填自己的 TGW 账号：

```bash
cp amazingdata/realtime.env.example amazingdata/realtime.env   # 填 realtime 专用账号 + Redis
cp amazingdata/batch.env.example amazingdata/batch.env         # 填 batch 专用账号 + Redis
```

非敏感的容器内路径（`HISTORICAL_PATH=/app/data`、`PYTHONPATH=/app` 等）直接写在 compose 的 `environment:` 里；`WorkerSettings` 仍保留读 `amazingdata/.env` 作为本地开发兜底，进程环境变量优先级更高。

### 一次性：构建 base 镜像（3-5 分钟）

```bash
bin/build-base.sh              # tag: adshare-base:latest
bin/build-base.sh 1.1          # tag: adshare-base:1.1
```

`bin/build-base.sh` 读取 `amazingdata/wheels/` 下的 whl + `amazingdata/base.Dockerfile`。SDK whl 升级流程：把新 .whl 放进 `wheels/` → 改 `base.Dockerfile` 里的文件名 → 重 build。

### 启动 batch（盘后模式）

```bash
docker compose -f amazingdata/docker-compose.batch.yml up -d
```

镜像内执行 `python -m amazingdata.batch`：登录 SDK → 初始化 warehouse → 启动 APScheduler → 阻塞等信号。

### 启动 realtime（盘中模式）

```bash
docker compose -f amazingdata/docker-compose.realtime.yml up -d
```

镜像内执行 `python -m amazingdata.realtime`：登录 SDK → 加载代码表 → 启动 `RealtimePublisher.run_blocking()` → 阻塞等信号。

### 双账号并行 / 单账号互斥

两个服务使用**不同** TGW 账号（realtime.env / batch.env 分别配置），可同时运行，无需切换。约束只剩一条：**同一账号不能被两个进程同时登录**（TGW 会踢掉先登录的会话）。

如果只有单账号，则恢复互斥切换：

```bash
# 收盘后切到 batch
docker compose -f amazingdata/docker-compose.realtime.yml down
docker compose -f amazingdata/docker-compose.batch.yml up -d

# 开盘前切到 realtime
docker compose -f amazingdata/docker-compose.batch.yml down
docker compose -f amazingdata/docker-compose.realtime.yml up -d
```

## 本地开发

```bash
# 加载对应服务的 env 文件到进程环境（pydantic-settings 优先读进程环境变量）
set -a; source amazingdata/realtime.env; set +a   # 或 batch.env

# 盘中模式
python -m amazingdata.realtime

# 盘后模式
python -m amazingdata.batch

# 直接调用 batch 的同步任务（无需 scheduler）
python -c "from amazingdata.batch import sync_kline_daily; print(sync_kline_daily())"
```

注意：本地必须有 AmazingData SDK 的 C 扩展（仅 linux/amd64）。Mac/Windows 上 `import amazingdata.adapters.amazingdata` 会失败；可以用 mock adapter 跑测试。

## 包结构对照

| 旧路径 | 新路径 |
|--------|--------|
| `amazingdata_worker/main.py` | `amazingdata/realtime.py` + `amazingdata/batch.py`（按模式拆分） |
| `amazingdata_worker/sync.py` | `amazingdata/batch.py`（合并到 batch 入口） |
| `amazingdata_worker/realtime_publisher.py` | `amazingdata/realtime.py`（合并到 realtime 入口） |
| `amazingdata_worker/adapters/base.py` | `amazingdata/adapters/base.py` |
| `amazingdata_worker/adapters/amazingdata.py` | `amazingdata/adapters/amazingdata.py` |
| `amazingdata_worker/Dockerfile` | `amazingdata/batch.Dockerfile` + `amazingdata/realtime.Dockerfile` |
| `amazingdata_worker/docker-compose.yml` | `amazingdata/docker-compose.batch.yml` + `amazingdata/docker-compose.realtime.yml` |
| `adshare_base/Dockerfile` | `amazingdata/base.Dockerfile` |
| 根目录 `*.whl` | `amazingdata/wheels/*.whl` |

## import 速查

```python
from amazingdata.adapters.base import DataSourceAdapter, SubscriptionSource
from amazingdata.adapters.amazingdata import get_adapter, AmazingDataAdapter
from amazingdata.realtime import RealtimePublisher, get_realtime_publisher
from amazingdata.batch import (
    sync_kline_daily, sync_kline_weekly, sync_kline_monthly,
    sync_meta_codes, sync_meta_calendar,
    sync_shareholder, sync_index_component, sync_financial,
    init_scheduler, start_scheduler, shutdown_scheduler,
)
```

## SDK 调用约束（重要）

AmazingData C 扩展在多线程并发调用 `query_kline` / `SubscribeData` 时会崩溃：
```
PyEval_SaveThread: the function must be called with the GIL held, but the
GIL is released (the current Python thread state is NULL)
```

`batch.py` 用进程级 `_SDK_CALL_LOCK` 串行化 SDK 调用，文件 I/O 留在临界区外。**不要在新代码里绕过这个锁**。

## 数据范围

- 仅 SH/SZ A 股（主板 / 创业板 / 科创板）
- 不含北交所（`.BJ`）
- 财务三表（balance / income / cashflow）已禁用 — HDF5 缓存占用过大且当前无人使用；如需恢复，从 `scripts/backfill_financial.py` 手动跑
- `adj_factor` 字段为占位 1.0（SDK 暂未提供）
