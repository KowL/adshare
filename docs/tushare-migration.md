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

### 统一入口（tushare Pro 协议）

```
POST /tushare
Body: {"api_name": "daily", "token": "...", "params": {...}, "fields": ""}
```

服务端根据 `api_name` 自动分发到股票或指数等分类处理器。

### RESTful 分类入口

#### 股票数据 `/tushare/stock/*`

| 路由 | api_name | 说明 |
|------|----------|------|
| `/tushare/stock/daily` | `daily` | 日线行情 |
| `/tushare/stock/weekly` | `weekly` | 周线行情 |
| `/tushare/stock/monthly` | `monthly` | 月线行情 |
| `/tushare/stock/stock_basic` | `stock_basic` | 股票基础信息 |
| `/tushare/stock/trade_cal` | `trade_cal` | 交易日历 |
| `/tushare/stock/adj_factor` | `adj_factor` | 复权因子 |
| `/tushare/stock/suspend_d` | `suspend_d` | 停牌信息 |
| `/tushare/stock/limit_list` | `limit_list` | 涨跌停股票池 |

#### 指数数据 `/tushare/index/*`（预留扩展）

| 路由 | api_name | 说明 |
|------|----------|------|
| `/tushare/index/basic` | `index_basic` | 指数基础信息（待实现） |
| `/tushare/index/daily` | `index_daily` | 指数日线（待实现） |

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

| HTTP 状态码 | 含义 |
|-------------|------|
| 400 | 参数错误 |
| 401 | 认证失败 |
| 403 | 无权限 |
| 404 | 数据不存在或仓库未启用 |
| 500 | 服务端内部错误 |
| 501 | 接口未实现 |

## 与原 `/dataapi` 的关系

旧的 `/dataapi/{api_name}` 接口已废弃，访问会返回提示信息，指引使用 `/tushare` 下的对应接口。
