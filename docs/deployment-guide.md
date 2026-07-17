# adshare 部署指南

> 版本: 1.0.0
> 更新日期: 2026-07-17

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
# API 服务（任意平台）
python -m adshare.main

# 实时行情订阅（仅 Linux/amd64，依赖 AmazingData SDK）
python -m amazingdata.realtime

# 盘后数据同步（仅 Linux/amd64，依赖 AmazingData SDK）
python -m amazingdata.batch
```

服务启动后访问 http://localhost:8000/docs 查看 Swagger UI。

---

## 生产部署（systemd + Docker Compose）

生产服务器（8.148.216.30，CentOS 8）上的实际形态：

- **API 服务**：宿主机 systemd 单元 `adshare-api` 运行，端口 8888，`AUTH_ENABLED=true` + X-API-Key 认证
- **Worker 服务**（realtime / batch）：Docker Compose，各自独立 TGW 账号，可同时运行
- **Redis**：与 API 同机的宿主机服务（127.0.0.1:26739）

### 1. 环境变量配置

API 一份 `.env`，worker 按服务拆成两份 env 文件（各用各的 TGW 账号）：

`adshare/.env`（API 服务；生产上为 `/opt/adshare/adshare/.env`，由应用内置 pydantic-settings 按该路径读取，systemd 单元无 EnvironmentFile）：
```env
ADSHARE_HOST=0.0.0.0
ADSHARE_PORT=8888
ADSHARE_LOG_LEVEL=INFO

REDIS_HOST=127.0.0.1
REDIS_PORT=26739

HISTORICAL_ENABLED=true
HISTORICAL_PATH=./data

AUTH_ENABLED=true
ADSHARE_API_KEY=<key>
```

`amazingdata/realtime.env`（盘中服务，从 `realtime.env.example` 拷贝）：
```env
AD_USERNAME=your-realtime-username   # realtime 专用 TGW 账号
AD_PASSWORD=your-realtime-password
AD_HOST=your-sdk-server
AD_PORT=8600
```

`amazingdata/batch.env`（盘后服务，从 `batch.env.example` 拷贝）：
```env
AD_USERNAME=your-batch-username      # batch 专用 TGW 账号（与 realtime 不同）
AD_PASSWORD=your-batch-password
AD_HOST=your-sdk-server
AD_PORT=8600

SYNC_SCHEDULE_ENABLED=true
```

三个 env 文件均已 gitignore；完整字段见 `amazingdata/*.env.example`。

### 2. 部署 API 服务（systemd）

代码位于 `/opt/adshare`（git clone）。CentOS 8 系统 Python 仅 3.6，需用 uv 建 Python 3.11 venv 并安装：

```bash
cd /opt/adshare
uv venv venv --python 3.11
uv pip install --python venv/bin/python ./adshare -i https://pypi.tuna.tsinghua.edu.cn/simple
```

安装并启动 systemd 单元（单元文件在仓库 `scripts/adshare-api.service`）：

```bash
cp scripts/adshare-api.service /etc/systemd/system/adshare-api.service
systemctl daemon-reload
systemctl enable --now adshare-api
systemctl status adshare-api
```

单元要点：

- `WorkingDirectory=/opt/adshare`，`Environment=PYTHONPATH=/opt/adshare`
- `ExecStart=/opt/adshare/venv/bin/python -m adshare.main`
- 日志 append 到 `/opt/adshare/logs/api.log`（StandardOutput/StandardError）

### 3. 部署 Worker 服务（Docker Compose）

realtime / batch 镜像基于共用的 base 镜像，需先手动构建一次：

```bash
bin/build-base.sh
```

然后从模板拷贝并填写各自的 env 文件（`amazingdata/realtime.env.example` / `amazingdata/batch.env.example`），分别启动：

```bash
# 实时行情订阅（盘中）
docker compose -f amazingdata/docker-compose.realtime.yml up -d

# 盘后数据同步
docker compose -f amazingdata/docker-compose.batch.yml up -d
```

两个 compose 是独立 project（`name: amazingdata-realtime` / `amazingdata-batch`），互不影响；两者使用各自独立的 TGW 账号，可同时运行（TGW 单连接约束按账号计）。

### 4. 备选：本地 / Docker 方式运行 API

本地或测试环境也可以用 Docker 跑 API（容器内端口 8000）：

```bash
docker compose -f adshare/docker-compose.yml up -d --build
```

### 5. 验证部署

```bash
# 健康检查
curl http://localhost:8888/health

# 历史数据仓状态
curl http://localhost:8888/historical/admin/health
```

---

## 架构说明

```
┌─────────────────────────────────────────────────────────────┐
│                         客户端                                │
│              (HTTP / WebSocket / SSE)                          │
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
│         amazingdata batch / realtime 服务                         │
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
docker compose -f amazingdata/docker-compose.batch.yml exec amazingdata-batch env | grep AD_

# 查看日志
docker compose -f amazingdata/docker-compose.batch.yml logs amazingdata-batch | tail -50
```

**解决**:
- 确认 `AD_USERNAME` 和 `AD_PASSWORD` 正确
- 确认容器能访问 AmazingData 服务器
- 在 x86 Linux 服务器上运行（ARM Mac 不支持 SDK）

### Q2: `/market/kline` 返回空数据

**原因**: 历史数据仓未同步

**排查**:
```bash
# 检查仓库状态
curl http://localhost:8888/historical/admin/stats

# 检查文件是否存在（生产：数据在 /opt/adshare/data）
ls /opt/adshare/data/A_share/daily/ | head

# 本地 Docker 部署方式时
docker compose -f adshare/docker-compose.yml exec adshare-api ls /app/data/A_share/daily/
```

**解决**:

同步任务需要数据源会话，只能在 worker 进程内执行（API 进程已不再提供
`/historical/admin/sync` 端点）：

```bash
# 方式一：在 batch.env 中设置 SYNC_ON_START=true，然后重启 batch worker
docker compose -f amazingdata/docker-compose.batch.yml up -d

# 方式二：在 batch 容器内手动跑同步脚本（容器内 working_dir 为 /app/data，脚本用绝对路径）
docker compose -f amazingdata/docker-compose.batch.yml exec amazingdata-batch python /app/scripts/backfill_kline.py --help
```

### Q3: `/market/snapshot` 返回空数据

**原因**: 快照数据不存储在 L3 仓库中，需要实时订阅

**解决**:

直接启动 realtime worker 即可。realtime 与 batch 使用各自独立的 TGW 账号、各自的 env 文件，可同时运行，无需互停：

```bash
docker compose -f amazingdata/docker-compose.realtime.yml up -d
```

（历史说明：单账号时代 realtime/batch 互斥，需先停 batch 再靠 `REALTIME_ENABLED` 切换；该变量现已无任何代码读取，属已废弃配置，无需设置。）

### Q4: `/fundamental/analyze` 返回 503

**原因**: 财务数据尚未同步到仓库

**说明**: 当前版本基本面分析需要 worker 服务将财务数据写入 warehouse，此功能将在后续版本支持。技术指标和涨停榜分析已完全可用。

### Q5: Docker x86 模拟性能问题（ARM Mac）

**现象**: 在 ARM Mac 上运行 Docker x86 模拟时，worker 服务非常慢

**解决**:
- **开发环境**: 使用 API-only 模式（`HISTORICAL_ENABLED=false`），不启动 worker
- **生产环境**: 必须在 x86 Linux 服务器上部署 worker

```bash
# 开发模式（ARM Mac，只跑 API，不跑 worker）
cd adshare && docker compose up -d adshare-api
# 不启动 amazingdata batch / realtime
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
# API 服务日志（生产：systemd）
journalctl -u adshare-api -f
tail -f /opt/adshare/logs/api.log

# Worker 服务日志（Docker）
docker logs -f amazingdata-realtime
docker logs -f amazingdata-batch

# API 服务日志（本地 Docker 部署方式时）
docker logs -f adshare-api

# Redis 日志（生产：宿主机 systemd 服务）
journalctl -u redis -f
```

### 健康检查端点

```bash
curl http://localhost:8888/health
curl http://localhost:8888/historical/admin/health
curl http://localhost:8888/technical/indicators
```

### Prometheus 指标

访问 http://localhost:8888/metrics 查看 Prometheus 指标。

---

## 升级指南

### 升级步骤

API 服务（systemd）：

```bash
cd /opt/adshare
git pull

# 依赖有变化时先更新安装
uv pip install --python venv/bin/python -U ./adshare

systemctl restart adshare-api
```

Worker 服务（Docker Compose，`<mode>` 为 realtime 或 batch）：

```bash
cd /opt/adshare
git pull
docker compose -f amazingdata/docker-compose.<mode>.yml up -d --build
```

验证：

```bash
systemctl status adshare-api
curl http://localhost:8888/health
docker ps --filter name=amazingdata
```

### 数据迁移

升级时历史数据仓（Parquet 文件）会自动兼容，无需手动迁移。

---

*本文档随版本迭代更新，最新版本请参阅 `docs/development-plan.md`*
