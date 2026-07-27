# Tushare 兼容适配

adshare 提供与 tushare Pro 协议兼容的数据接口，已有 tushare 使用习惯的项目/策略代码可以最小改动切换到 adshare。

## 快速开始

### 1. 启动 adshare 服务

```bash
cd adshare && docker compose up -d
```

服务启动后，tushare 兼容接口位于 `http://localhost:8000/tushare`。

### 2. 使用官方 tushare 客户端

客户端只需安装并导入官方 `tushare` 包，无需复制 adshare 项目中的文件：

```python
import tushare as ts

# token 使用 adshare API key
pro = ts.pro_api("your-adshare-api-key")

# 将官方客户端的请求地址指向 adshare 服务
pro._DataApi__http_url = "http://localhost:8000/tushare"

# 像使用 tushare 一样获取数据
df = pro.daily(ts_code="000001.SZ", start_date="20240101", end_date="20240131")
print(df.head())
```

> `pro._DataApi__http_url` 是 tushare 客户端的内部属性；升级 tushare 后如调用异常，请确认该版本的请求地址配置方式。

## 服务端路由

adshare 仅暴露一个 tushare 兼容入口，完全对齐官方 Tushare Pro 协议。

### 统一入口

```
POST /tushare
Body: {"api_name": "<name>", "token": "...", "params": {...}, "fields": ""}
```

服务端根据 `api_name` 自动分发到对应处理器。常见的 `api_name` 列表：

| `api_name` | 说明 |
|------------|------|
| `daily` | 日线行情 |
| `weekly` | 周线行情 |
| `monthly` | 月线行情 |
| `stock_basic` | 股票基础信息 |
| `trade_cal` | 交易日历 |
| `adj_factor` | 复权因子 |
| `suspend_d` | 停牌信息 |
| `limit_list` | 涨跌停股票池 |
| `limit_list_d` | 涨跌停详情(同花顺版),支持 `limit_type` 过滤 U/D/Z |
| `index_basic` | 指数基础信息（待实现） |
| `index_daily` | 指数日线（待实现） |
| `rt_k` | 实时 Level-1 快照 |
| `rt_min` | 实时分钟 K 线 |

> 之前的 RESTful 路径 `/tushare/stock/*` / `/tushare/index/*` / `/tushare/realtime/*` 已下线，调用方请改用 `POST /tushare` + `api_name` 字段。

## 公共参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `ts_code` | str | 股票代码，支持逗号分隔，如 `000001.SZ,600000.SH` |
| `start_date` | str/int | 开始日期，支持 `YYYYMMDD` 或 `YYYY-MM-DD` |
| `end_date` | str/int | 结束日期，支持 `YYYYMMDD` 或 `YYYY-MM-DD` |
| `trade_date` | str/int | 交易日期 |
| `exchange` | str | 交易所：`SSE`/`SZSE`/`BSE` |
| `fields` | str | 逗号分隔的返回字段，为空则返回全部 |
| `limit` | int | 最大返回条数 |
| `offset` | int | 跳过条数 |
| `token` | str | adshare API key |

## 返回格式

所有接口返回 tushare Pro 标准格式：

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
    "items": [
      ["000001.SZ", 20240102, 10.0, 10.5, 9.8, 10.2, 1000, 10200.0]
    ]
  }
}
```

官方 tushare 客户端会将其转换为 pandas DataFrame。

## 错误码

`POST /tushare` 始终返回 HTTP `200`，实际错误通过响应体的 `code` 字段表达（与 Tushare Pro 官方协议一致）。调用方应优先根据 `code` 判断成功/失败，仅在网络层异常时依赖 HTTP 状态码。

| body `code` | HTTP 状态 | 含义 |
|-------------|-----------|------|
| `0` | 200 | 成功 |
| `20001` | 200 | 缺少 `token`（未配置 `AUTH_ENABLED` 时仍会校验） |
| `20002` | 200 | `token` 无效 |
| `-400` | 200 | 参数缺失或格式错误 |
| `-501` | 200 | `api_name` 不存在或未实现 |
| `-404` | 200 | 数据不存在或仓库未启用 |
| `-500` | 200 | 服务端内部错误（SDK/仓库异常） |

## 与原 `/dataapi` 的关系

旧的 `/dataapi/{api_name}` 接口已废弃，访问会返回提示信息，指引使用 `/tushare` 下的对应接口。
