# adshare 部署指南

> 版本: 1.0.0
> 更新日期: 2026-06-11

---

## 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | >= 3.11 | 推荐 3.12 |
| Docker | >= 24.0 | 生产部署必需 |
| Docker Compose | >= 2.20 | 生产部署必需 |
| Redis | >= 7.0 | 实时行情缓存 |
| 操作系统 | Linux amd64 | Worker 服务必需（SDK 限制）|

---

## 快速开始（开发环境）

### 1. 克隆代码

```bash
git clone <repo-url>
cd adshare
```

### 2. 安装依赖

```bash
pip install -e ".[dev]"
```

### 3. 启动服务

```bash
# 单服务模式（仅 API，无 SDK 依赖）
python -m adshare.main

# 或带实时行情订阅
REALTIME_ENABLED=true python -m adshare.main
```

服务启动后访问 http://localhost:8000/docs 查看 Swagger UI。

---

## 生产部署（Docker Compose）

### 1. 环境变量配置

创建 `.env` 文件：

```env
# 服务配置
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# 历史数据仓
HISTORICAL_ENABLED=true
HISTORICAL_PATH=/data/historical

# 同步调度
SYNC_SCHEDULE_ENABLED=true

# 认证（可选）
AUTH_ENABLED=false
ADSHARE_API_KEY=your-secret-key

# AmazingData SDK（仅 worker 服务需要）
AD_USER=your-username
AD_PASSWORD=your-password
```

### 2. 启动服务

```bash
docker compose up -d
```

### 3. 验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# 历史数据仓状态
curl http://localhost:8000/historical/admin/health
```

---

## 架构说明

```
┌─────────────────────────────────────────────────────────────┐
│                         客户端                                │
│              (HTTP / MCP / WebSocket 未来支持)                │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                    adshare API 服务                          │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────────────┐  │
│  │ MarketData  │ │ Technical    │ │ Fundamental/Factor   │  │
│  │ Service     │ │ Analysis     │ │ Analysis Service     │  │
│  └─────────────┘ └──────────────┘ └──────────────────────┘  │
│                           │                                  │
│              ┌────────────┼────────────┐                    │
│              ▼            ▼            ▼                    │
│         ┌────────┐  ┌─────────┐  ┌──────────┐             │
│         │ Redis  │  │ DuckDB  │  │ Parquet  │             │
│         │ (实时) │  │ (查询)  │  │ (L3 仓)  │             │
│         └────────┘  └─────────┘  └──────────┘             │
└─────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│              amazingdata_worker 服务                         │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────────────┐  │
│  │ SDK Adapter │ │ Realtime     │ │ Sync Scheduler       │  │
│  │ (Linux/x86) │ │ Subscriber   │ │ (日K/周K/月K/代码表)  │  │
│  └─────────────┘ └──────────────┘ └──────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

- **API 服务**：纯 Python，可在任何平台运行（ARM Mac / x86 Linux）
- **Worker 服务**：必须在 Linux/amd64 运行（AmazingData SDK 限制）
- **数据流**：Worker 拉取 SDK 数据 → 写入 Parquet + Redis → API 服务读取

---

## 常见问题排查

### Q1: Worker 服务启动失败 "AmazingData login failed"

**原因**: SDK 账号密码错误或网络不通

**排查**:
```bash
# 检查环境变量
docker compose exec worker env | grep AD_

# 查看日志
docker compose logs worker | tail -50
```

**解决**:
- 确认 `AD_USER` 和 `AD_PASSWORD` 正确
- 确认容器能访问 AmazingData 服务器
- 在 x86 Linux 服务器上运行（ARM Mac 不支持 SDK）

### Q2: `/market/kline` 返回空数据

**原因**: 历史数据仓未同步

**排查**:
```bash
# 检查仓库状态
curl http://localhost:8000/historical/admin/stats

# 检查文件是否存在
docker compose exec api ls /data/historical/A_share/daily/
```

**解决**:

同步任务需要数据源会话，只能在 worker 进程内执行（API 进程已不再提供
`/historical/admin/sync` 端点）：

```bash
# 方式一：重启 worker 并触发启动即同步
SYNC_ON_START=true docker compose up -d worker

# 方式二：在 worker 容器内手动跑同步脚本
docker compose exec worker python scripts/backfill_kline.py --help
```

### Q3: `/market/snapshot` 返回空数据

**原因**: 快照数据不存储在 L3 仓库中，需要实时订阅

**解决**:
```bash
# 启动实时行情订阅（在 worker 中）
REALTIME_ENABLED=true docker compose up -d worker
```

### Q4: `/fundamental/analyze` 返回 503

**原因**: 财务数据尚未同步到仓库

**说明**: 当前版本基本面分析需要 worker 服务将财务数据写入 warehouse，此功能将在后续版本支持。技术指标和涨停榜分析已完全可用。

### Q5: Docker x86 模拟性能问题（ARM Mac）

**现象**: 在 ARM Mac 上运行 Docker x86 模拟时，worker 服务非常慢

**解决**:
- **开发环境**: 使用 API-only 模式（`HISTORICAL_ENABLED=false`），不启动 worker
- **生产环境**: 必须在 x86 Linux 服务器上部署 worker

```bash
# 开发模式（ARM Mac）
docker compose up -d api redis
# 不启动 worker
```

### Q6: 测试失败 "APScheduler is not installed"

**原因**: 缺少 apscheduler 依赖

**解决**:
```bash
pip install apscheduler
```

### Q7: 覆盖率测试运行缓慢

**原因**: DuckDB 首次查询需要编译视图

**解决**:
```bash
# 跳过覆盖率测试，仅运行功能测试
pytest tests/ -q --no-cov

# 或只运行特定模块
pytest tests/test_market.py -q
```

---

## 监控与日志

### 查看日志

```bash
# API 服务日志
docker compose logs -f api

# Worker 服务日志
docker compose logs -f worker

# Redis 日志
docker compose logs -f redis
```

### 健康检查端点

```bash
curl http://localhost:8000/health
curl http://localhost:8000/historical/admin/health
curl http://localhost:8000/technical/indicators
```

### Prometheus 指标

访问 http://localhost:8000/metrics 查看 Prometheus 指标。

---

## 升级指南

### 升级步骤

```bash
# 1. 拉取最新代码
git pull origin main

# 2. 重建镜像
docker compose build

# 3. 重启服务
docker compose up -d

# 4. 验证
curl http://localhost:8000/health
pytest tests/ -q
```

### 数据迁移

升级时历史数据仓（Parquet 文件）会自动兼容，无需手动迁移。

---

*本文档随版本迭代更新，最新版本请参阅 `docs/development-plan.md`*
