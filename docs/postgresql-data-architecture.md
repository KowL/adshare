# adshare PostgreSQL 数据架构

## 数据流

```text
AmazingData SDK
    ├─ batch：代码、日历、日/周/月 K、参考数据
    │       └─ PostgreSQL（批量 UPSERT）
    └─ realtime：盘中快照/分钟线
            └─ Redis Pub/Sub + 短期状态

adshare-api
    ├─ 历史/基础/参考数据 → PostgreSQL
    └─ 实时数据           → Redis
```

API 不再挂载或读取 Parquet 文件，也不再嵌入 DuckDB。batch worker 与
adshare-api 必须连接同一个 PostgreSQL 数据库。

## 表结构

- `master.stock`：股票主数据，以 `stock_id BIGSERIAL` 为主键，`code` 保留
  `000001.SZ` 形式供 API 使用。
- `market.daily_bar`：日线事实表。
- `market.weekly_bar`、`market.monthly_bar`：周/月线查询表。
- `market.trade_calendar`：交易日历。
- `market.sync_job`：每次同步的状态、范围和行数审计。
- `market.reference_data`：财务、股东、指数成分等低频数据的 JSONB
  过渡存储，避免任何 API 回退到 Parquet。

行情表以 `(stock_id, trade_date)` 为主键。worker 使用
`INSERT ... ON CONFLICT DO UPDATE`，所以全量回填、增量同步和修复任务
均可安全重跑。

完整 DDL 位于
`adshare/historical/migrations/001_postgresql.sql`。默认启动时自动执行
幂等迁移，可用 `DATABASE_AUTO_MIGRATE=false` 关闭。

## 配置

API 与 batch 的共同配置：

```env
DATABASE_URL=postgresql://adshare:change_me@postgres:5432/adshare
DATABASE_POOL_MIN_SIZE=1
DATABASE_POOL_MAX_SIZE=10
DATABASE_CONNECT_TIMEOUT=10
DATABASE_QUERY_TIMEOUT=30
DATABASE_QUERY_MAX_ROWS=100000
DATABASE_AUTO_MIGRATE=true
```

生产环境建议为 API 和 worker 分配不同数据库角色：API 角色只授予
`SELECT`，worker 角色授予 `master`、`market` 的写权限。两者可以使用
各自的 `DATABASE_URL`。

## 部署顺序

1. 启动 PostgreSQL 15+。
2. 启动 `adshare-api`,完成 schema 初始化(`DATABASE_AUTO_MIGRATE=true`
   自动执行迁移,完成后 `/health` 与 `/status/data-freshness` 应返回 200)。
3. 在 `amazingdata/batch.env` 配置同一数据库并启动 batch worker。
4. 首次执行 `python -m scripts.backfill_kline --meta --period all`。
5. 启动 realtime worker；实时链路仍只使用 Redis。

`adshare/docker-compose.yml` 已包含 PostgreSQL 16 和持久化 volume。

## 从旧 Parquet 仓迁移

保留旧 `data/` 目录，执行：

```bash
DATABASE_URL=postgresql://... \
python -m scripts.migrate_parquet_to_postgres --source ./data
```

迁移脚本同样走幂等 UPSERT，可中断后重跑。验证 PostgreSQL 行数和日期
范围无误后，旧 Parquet 目录可以离线归档；API 与定时任务不再使用它。
