# AdShare 股票数据 API 方案（参考 Pro 数据平台格式）

> **⚠️ 归档说明（2026-07-17）**：本文档是 2026-06 的一次性实施计划。功能已上线，
> 但**实现形态与本计划不同**——实际采用 `POST /tushare` 统一入口 + `/tushare/stock/*`
> 分类路由（见 `docs/tushare-migration.md`），而非本文设计的根路径 REST。
> 本文保留仅因第四节字段映射表仍有参考价值，**请勿据此开发新功能**。

## 目标

adshare 直接提供与主流 Pro 数据平台同名的股票数据 API 接口，路由、参数、响应格式完全对齐其文档，数据源来自 adshare 的 L3 历史仓库（Parquet/DuckDB）和实时数据层。

调用方式示例：
```python
import requests

# 股票基础信息
r = requests.get("http://localhost:8000/stock_basic", params={
    "exchange": "", "list_status": "L",
    "fields": "ts_code,symbol,name,area,industry,list_date"
})

# 日线行情
r = requests.get("http://localhost:8000/daily", params={
    "ts_code": "000001.SZ", "start_date": "20250101", "end_date": "20251231"
})
```

---

## 一、股票数据接口全景

### 1. 基础信息类

| 接口名 | 说明 | adshare 当前状态 |
|--------|------|-----------------|
| `stock_basic` | 股票基础信息 | ⚠️ 元数据需补充 |
| `trade_cal` | 交易日历 | ✅ L3 仓库已有 |
| `namechange` | 股票曾用名 | ❌ 需新数据源 |
| `new_share` | 新股列表 | ⚠️ 可从 stock_basic 过滤 |
| `stk_holdernumber` | 股东人数 | ❌ 需新数据源 |
| `stk_reward` | 股权质押 | ❌ 需新数据源 |

### 2. 行情数据类

| 接口名 | 说明 | adshare 当前状态 |
|--------|------|-----------------|
| `daily` | 日线行情 | ✅ L3 仓库已有 |
| `weekly` | 周线行情 | ✅ L3 仓库已有 |
| `monthly` | 月线行情 | ✅ L3 仓库已有 |
| `pro_bar` | 通用行情（复权、均线） | ⚠️ 需组装计算 |
| `adj_factor` | 复权因子 | ✅ L3 已有 adj_factor |
| `suspend_d` | 每日停牌信息 | ⚠️ 可从 K-line 推导 |
| `daily_basic` | 每日基本面指标 | ❌ 需接入新数据源 |
| `moneyflow` | 个股资金流向 | ❌ 需新数据源 |

### 3. 财务数据类

| 接口名 | 说明 | adshare 当前状态 |
|--------|------|-----------------|
| `income` | 利润表 | ⚠️ `/financial` 可扩展 |
| `balance_sheet` | 资产负债表 | ⚠️ `/financial` 可扩展 |
| `cashflow` | 现金流量表 | ⚠️ `/financial` 可扩展 |
| `fina_indicator` | 财务指标 | ⚠️ `/fundamental` 可扩展 |
| `dividend` | 分红送股 | ❌ 需新数据源 |
| `express` | 业绩快报 | ❌ 需新数据源 |
| `forecast` | 业绩预告 | ❌ 需新数据源 |

### 4. 市场参考类

| 接口名 | 说明 | adshare 当前状态 |
|--------|------|-----------------|
| `limit_list` | 涨跌停列表 | ⚠️ `/market/limit-up` 可转格式 |
| `moneyflow` | 资金流向 | ❌ 需新数据源 |
| `margin` / `margin_detail` | 融资融券 | ❌ 需新数据源 |
| `top_list` / `top_inst` | 龙虎榜 | ❌ 需新数据源 |
| `block_trade` | 大宗交易 | ❌ 需新数据源 |

### 5. 指数类

| 接口名 | 说明 | adshare 当前状态 |
|--------|------|-----------------|
| `index_basic` | 指数基础信息 | ❌ 需新数据源 |
| `index_daily` | 指数日线 | ⚠️ 需确认 K-line 是否支持指数代码 |
| `index_member` | 指数成分股 | ❌ 需新数据源 |
| `index_weight` | 指数成分权重 | ❌ 需新数据源 |

---

## 二、实施优先级

### Phase 1：核心行情 + 基础信息（高优先级）

- `stock_basic` — 股票基础信息
- `trade_cal` — 交易日历
- `daily` — 日线行情
- `weekly` — 周线行情
- `monthly` — 月线行情
- `adj_factor` — 复权因子
- `pro_bar` — 通用行情（复权 + 均线）
- `suspend_d` — 停牌信息

### Phase 2：每日指标 + 财务基础（中优先级）

- `daily_basic` — 每日基本面指标
- `income` / `balance_sheet` / `cashflow` — 三大财报
- `fina_indicator` — 财务指标
- `new_share` — 新股列表
- `limit_list` — 涨跌停（统一现有数据）

### Phase 3：指数 + 市场参考（低优先级，持续迭代）

- `index_basic` / `index_daily` / `index_member`
- `moneyflow` / `margin` / `top_list`
- `stk_holdernumber` / `namechange`

---

## 三、接口设计

### 3.1 路由定义

直接在 adshare 根路由下提供，不添加额外前缀：

```
GET /stock_basic
GET /trade_cal
GET /daily
GET /weekly
GET /monthly
GET /adj_factor
GET /pro_bar
GET /suspend_d
GET /daily_basic
GET /limit_list
GET /moneyflow
GET /new_share
GET /income
GET /balance_sheet
GET /cashflow
GET /fina_indicator
GET /index_basic
GET /index_daily
```

### 3.2 请求参数

完全对齐 Pro 数据平台文档。以 `stock_basic` 为例：

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| `ts_code` | str | N | TS 代码，如 `000001.SZ`，支持多值逗号分隔 |
| `name` | str | N | 股票名称模糊查询 |
| `exchange` | str | N | 交易所代码：`SSE` / `SZSE` / `BSE` |
| `market` | str | N | 市场类型：主板/创业板/科创板/北交所/CDR |
| `is_hs` | str | N | 是否沪深港通：`N` 否 / `H` 沪股通 / `S` 深股通 |
| `list_status` | str | N | 上市状态：`L` 上市 / `D` 退市 / `P` 暂停上市 |
| `fields` | str | N | 指定返回字段，逗号分隔 |

### 3.3 响应格式

Pro 数据平台标准响应格式：

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "fields": ["ts_code", "symbol", "name", "area", "industry", "list_date"],
    "items": [
      ["000001.SZ", "000001", "平安银行", "深圳", "银行", "19910403"],
      ["000002.SZ", "000002", "万科A", "深圳", "全国地产", "19910129"]
    ]
  },
  "request_id": "uuid"
}
```

错误响应：

```json
{
  "code": -1,
  "msg": "ts_code format invalid",
  "data": null,
  "request_id": "uuid"
}
```

---

## 四、数据格式对照与映射

### 4.1 `stock_basic`

**Pro 平台输出字段：**
```
ts_code, symbol, name, area, industry, fullname, enname, cnspell,
market, exchange, curr_type, list_status, list_date, delist_date, is_hs
```

**adshare 现有字段：**
```
code, name, comp_name, list_date, delist_date, list_plate, is_listed
```

**映射方案：**

| Pro 字段 | adshare 来源 | 映射规则 |
|-------------|-------------|---------|
| `ts_code` | `code` | 直接映射，格式已是 `000001.SZ` |
| `symbol` | `code` | 取 `.` 前的部分，如 `000001` |
| `name` | `name` | 直接映射 |
| `area` | — | 暂无，返回空字符串 |
| `industry` | — | 暂无，返回空字符串 |
| `fullname` | `comp_name` | 直接映射 |
| `enname` | — | 暂无，返回空字符串 |
| `cnspell` | — | 暂无，返回空字符串 |
| `market` | `list_plate` | `主板`→`主板`, `创业板`→`创业板`, `科创板`→`科创板` |
| `exchange` | `code` | 从后缀提取：`.SZ`→`SZSE`, `.SH`→`SSE`, `.BJ`→`BSE` |
| `curr_type` | — | 固定返回 `CNY` |
| `list_status` | `is_listed` | `1`→`L`, `0`→`D` |
| `list_date` | `list_date` | `19910403` → `19910403`（已是字符串/数字格式） |
| `delist_date` | `delist_date` | 直接映射 |
| `is_hs` | — | 暂无，返回 `N` |

### 4.2 `daily` / `weekly` / `monthly`

**Pro 平台输出字段：**
```
ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount
```

**adshare K-line 现有字段：**
```
code, date, open, high, low, close, volume, amount
```

**映射方案：**

| Pro 字段 | adshare 来源 | 计算/映射规则 |
|-------------|-------------|--------------|
| `ts_code` | `code` | 直接映射 |
| `trade_date` | `date` | `20250611` → `20250611`，保持 `YYYYMMDD` 格式 |
| `open` | `open` | 直接映射 |
| `high` | `high` | 直接映射 |
| `low` | `low` | 直接映射 |
| `close` | `close` | 直接映射 |
| `pre_close` | `close` | `shift(-1)` 取下一行的 close |
| `change` | `close`, `pre_close` | `close - pre_close` |
| `pct_chg` | `change`, `pre_close` | `change / pre_close * 100`，保留 2 位小数 |
| `vol` | `volume` | `volume / 100`（股 → 手） |
| `amount` | `amount` | 直接映射（已是千元单位需确认） |

> **注意：** Pro 平台返回数据按 `trade_date` **降序**排列（最新日期在前），adshare 当前 K-line 需确认排序方向并统一。

### 4.3 `trade_cal`

**Pro 平台输出字段：**
```
exchange, cal_date, is_open, pretrade_date
```

**adshare calendar 现有字段：**
```
date, is_open
```

**映射方案：**

| Pro 字段 | adshare 来源 | 计算/映射规则 |
|-------------|-------------|--------------|
| `exchange` | 请求参数 | 直接透传 |
| `cal_date` | `date` | `YYYYMMDD` 格式 |
| `is_open` | `is_open` | `1`/`0` |
| `pretrade_date` | `date` | 取该日期前一个 `is_open=1` 的日期 |

### 4.4 `adj_factor`

**Pro 平台输出字段：**
```
ts_code, trade_date, adj_factor
```

**adshare 已有：** L3 warehouse K-line 表中存储 `adj_factor`

**映射方案：** 直接暴露，字段名完全一致。

### 4.5 `pro_bar`

Pro 平台的 `pro_bar` 是 SDK 层的高级接口，**不是直接 HTTP 接口**，它底层调用 `daily` + `adj_factor` 进行复权计算，并支持均线。

adshare 的 `pro_bar` 需要实现为服务端接口：

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| `ts_code` | str | Y | 股票代码 |
| `start_date` | str | N | 开始日期 `YYYYMMDD` |
| `end_date` | str | N | 结束日期 `YYYYMMDD` |
| `asset` | str | N | 资产类型：`E` 股票（默认）/ `I` 指数 |
| `adj` | str | N | 复权类型：`None` / `qfq` / `hfq` |
| `freq` | str | N | 频度：`D` 日 / `W` 周 / `M` 月 |
| `ma` | str | N | 均线，如 `5,10,20` |

**实现逻辑：**
1. 根据 `freq` 调用 `daily` / `weekly` / `monthly` 获取基础 K-line
2. 根据 `adj` 读取 `adj_factor` 计算复权价格
3. 根据 `ma` 参数在内存中计算滚动均线
4. 返回与 Pro 平台 `pro_bar` 完全一致的字段

### 4.6 `suspend_d`

**Pro 平台输出字段：**
```
ts_code, suspend_date, resume_date, ann_date, suspend_reason, reason_type
```

**adshare 推导方案：** 从 K-line 数据中识别 `vol=0` 且 `amplitude=0`（或数据缺失）的连续交易日，作为停牌区间。

| Pro 字段 | 推导规则 |
|-------------|---------|
| `ts_code` | 股票代码 |
| `suspend_date` | 停牌开始日期（第一个 vol=0 的交易日） |
| `resume_date` | 停牌结束日期（最后一个 vol=0 的下一个交易日） |
| `ann_date` | — 无法推导，返回空 |
| `suspend_reason` | — 无法推导，返回空 |
| `reason_type` | — 无法推导，返回空 |

---

## 五、模块设计

直接在现有结构中扩展，文件命名不包含任何平台名称：

```
adshare/
├── routers/
│   ├── __init__.py
│   ├── stock_data.py           # 股票类接口路由：stock_basic, daily, weekly...
│   ├── market_reference.py     # 市场参考类接口路由：limit_list, moneyflow...
│   ├── financial_data.py       # 财务类接口路由：income, balance_sheet...
│   ├── index_data.py           # 指数类接口路由：index_basic, index_daily...
│   └── ...                     # 现有路由保持不变
├── services/
│   ├── dataframe_formatter.py  # DataFrame → {fields, items} 格式转换
│   ├── derived_metrics.py      # 计算逻辑：pre_close/change/pct_chg/复权/均线/停牌
│   └── ...                     # 现有服务保持不变
├── models/
│   ├── stock_schemas.py        # 请求/响应 Pydantic 模型
│   └── ...                     # 现有模型保持不变
└── main.py                     # 注册新路由
```

### 5.1 路由注册（main.py）

```python
from adshare.routers import (
    stock_data,
    market_reference,
    financial_data,
    index_data,
)

app.include_router(stock_data.router)
app.include_router(market_reference.router)
app.include_router(financial_data.router)
app.include_router(index_data.router)
```

每个 router 的 `prefix=""`，这样接口就是 `/stock_basic`、`/daily` 等根路由。

### 5.2 核心工具类 `DataFrameFormatter`

```python
# adshare/services/dataframe_formatter.py

import pandas as pd
from typing import List, Any, Dict

class DataFrameFormatter:
    """将 adshare 数据转换为 {fields, items} 响应格式。"""

    @staticmethod
    def to_fields_items(
        df: pd.DataFrame,
        field_map: Dict[str, str] = None,
        converters: Dict[str, callable] = None,
    ) -> dict:
        """
        输出: {"fields": [...], "items": [[...], [...]]}
        """
        if df.empty:
            return {"fields": [], "items": []}

        # 字段重命名
        if field_map:
            df = df.rename(columns=field_map)

        # 类型转换
        if converters:
            for col, fn in converters.items():
                if col in df.columns:
                    df[col] = df[col].apply(fn)

        fields = list(df.columns)
        items = df.values.tolist()
        return {"fields": fields, "items": items}

    @staticmethod
    def build_response(
        data: dict,
        code: int = 0,
        msg: str = "success",
        request_id: str = None,
    ) -> dict:
        return {
            "code": code,
            "msg": msg,
            "data": data,
            "request_id": request_id,
        }
```

---

## 六、Phase 1 接口详细设计

### 6.1 `GET /stock_basic`

**请求参数：**
```
ts_code      str   N   TS代码
name         str   N   股票名称（模糊匹配）
exchange     str   N   SSE/SZSE/BSE
market       str   N   市场类型：主板/创业板/科创板/北交所/CDR
is_hs        str   N   N/H/S
list_status  str   N   L/D/P
fields       str   N   指定返回字段
```

**响应示例：**
```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "fields": ["ts_code","symbol","name","area","industry","market","exchange","list_status","list_date","delist_date","is_hs"],
    "items": [
      ["000001.SZ","000001","平安银行","深圳","银行","主板","SZSE","L","19910403",null,"N"],
      ["600519.SH","600519","贵州茅台","遵义","白酒","主板","SSE","L","20010827",null,"H"]
    ]
  },
  "request_id": "..."
}
```

**实现逻辑：**
1. 读取 L3 warehouse `codes` 表
2. 根据请求参数过滤（ exchange, market, list_status 等）
3. 字段映射（code→ts_code, comp_name→fullname, 等）
4. 用 `DataFrameFormatter` 组装响应

---

### 6.2 `GET /trade_cal`

**请求参数：**
```
exchange     str   N   SSE/SZSE（默认空，返回全部）
start_date   str   N   YYYYMMDD
end_date     str   N   YYYYMMDD
is_open      str   N   0/1（默认返回全部）
```

**实现逻辑：**
1. 读取 L3 warehouse `calendar` 表
2. 按 exchange / start_date / end_date / is_open 过滤
3. 计算 `pretrade_date`（前一个交易日）

---

### 6.3 `GET /daily`

**请求参数：**
```
ts_code      str   Y   TS代码，如 000001.SZ
trade_date   str   N   交易日期 YYYYMMDD
start_date   str   N   开始日期
end_date     str   N   结束日期
fields       str   N   指定返回字段
```

> 与 Pro 平台一致：ts_code 和 trade_date 至少传一个。

**实现逻辑：**
1. 复用 `MarketDataService.get_kline()` 读取 L3 warehouse
2. 按日期范围过滤
3. 计算 `pre_close`, `change`, `pct_chg`
4. `volume` → `vol`（除以 100）
5. 字段重命名：`code`→`ts_code`, `date`→`trade_date`
6. 按 `trade_date` **降序**排列

---

### 6.4 `GET /weekly` / `GET /monthly`

与 `daily` 完全一致，仅 `period="week"` / `period="month"` 不同。

---

### 6.5 `GET /adj_factor`

**请求参数：**
```
ts_code      str   Y   TS代码
trade_date   str   N   交易日期
start_date   str   N   开始日期
end_date     str   N   结束日期
```

**实现逻辑：**
1. 从 L3 warehouse K-line 表中提取 `ts_code`, `trade_date`, `adj_factor`
2. 按条件过滤后返回

---

### 6.6 `GET /pro_bar`

**请求参数：**
```
ts_code      str   Y   TS代码
start_date   str   N   开始日期
end_date     str   N   结束日期
asset        str   N   E/I，默认 E
adj          str   N   None/qfq/hfq
freq         str   N   D/W/M，默认 D
ma           str   N   均线，如 "5,10,20"
```

**实现逻辑：**
1. 根据 `freq` 获取对应 K-line（daily/weekly/monthly）
2. 如果 `adj` 不为空，读取 `adj_factor` 并计算复权价格
3. 如果 `ma` 不为空，对 close 列做滚动平均计算
4. 返回完整 OHLCV + 均线列

---

### 6.7 `GET /suspend_d`

**请求参数：**
```
ts_code      str   N   TS代码（为空返回全部）
trade_date   str   N   交易日期
start_date   str   N   开始日期
end_date     str   N   结束日期
```

**实现逻辑：**
1. 读取 K-line 数据，识别 `vol=0` 的连续区间
2. 每个连续区间输出一条停牌记录

---

## 七、关键实现代码片段

### 7.1 涨跌幅计算

```python
def compute_price_changes(df: pd.DataFrame) -> pd.DataFrame:
    """计算 pre_close, change, pct_chg，按 trade_date 降序排列。"""
    df = df.sort_values("date", ascending=False).reset_index(drop=True)
    df["pre_close"] = df["close"].shift(-1)
    df["change"] = df["close"] - df["pre_close"]
    df["pct_chg"] = (df["change"] / df["pre_close"] * 100).round(2)
    return df
```

### 7.2 复权计算

```python
def apply_adj(df: pd.DataFrame, adj_df: pd.DataFrame, adj_type: str) -> pd.DataFrame:
    """
    adj_type: 'qfq' | 'hfq'
    """
    df = df.merge(adj_df[["date", "adj_factor"]], on="date", how="left")
    df["adj_factor"] = df["adj_factor"].fillna(method="ffill")

    if adj_type == "qfq":
        factor = df["adj_factor"].iloc[0]  # 最新日期的因子作为基准
        for col in ["open", "high", "low", "close"]:
            df[col] = (df[col] * df["adj_factor"] / factor).round(2)
    elif adj_type == "hfq":
        for col in ["open", "high", "low", "close"]:
            df[col] = (df[col] * df["adj_factor"]).round(2)

    return df.drop(columns=["adj_factor"])
```

### 7.3 均线计算

```python
def compute_ma(df: pd.DataFrame, ma_params: List[int]) -> pd.DataFrame:
    """ma_params: [5, 10, 20]"""
    df = df.sort_values("date", ascending=True)  # 正序计算
    for n in ma_params:
        df[f"ma{n}"] = df["close"].rolling(window=n).mean().round(2)
    return df.sort_values("date", ascending=False)  # 返回降序
```

### 7.4 停牌推导

```python
def derive_suspend(df: pd.DataFrame) -> pd.DataFrame:
    """从 K-line 推导停牌记录。"""
    df = df.sort_values("date", ascending=True)
    df["is_suspend"] = (df["vol"] == 0) | (df["volume"] == 0)

    # 找连续停牌区间
    df["group"] = (df["is_suspend"] != df["is_suspend"].shift()).cumsum()
    suspends = df[df["is_suspend"]].groupby("group").agg(
        ts_code=("code", "first"),
        suspend_date=("date", "min"),
        resume_date=("date", "max"),
    )
    # resume_date 是停牌最后一天，实际需要 +1 个交易日
    return suspends
```

---

## 八、数据缺口清单

| 缺口 | 影响接口 | 临时处理方案 |
|------|---------|-------------|
| 行业、地域、拼音 | `stock_basic` | 返回空字符串或 `None`，后续扩展 warehouse |
| 沪深港通标记 | `stock_basic.is_hs` | 固定返回 `N`，后续接入 |
| PE/PB/换手率/市值 | `daily_basic` | Phase 2 接入 AmazingData SDK 或计算 |
| 资金流向 | `moneyflow` | Phase 3 接入新数据源 |
| 融资融券 | `margin*` | Phase 3 接入新数据源 |
| 龙虎榜 | `top_list` | Phase 3 接入新数据源 |
| 指数成分股 | `index_member` | Phase 3 接入新数据源 |
| 停牌原因 | `suspend_d.reason` | 无法推导，返回空 |
| 股票曾用名 | `namechange` | Phase 2/3 接入新数据源 |

---

## 九、Phase 1 实施步骤

### Step 1：创建基础设施（0.5 天）
- [ ] 新建 `adshare/services/dataframe_formatter.py` — 格式转换工具
- [ ] 新建 `adshare/services/derived_metrics.py` — 计算逻辑
- [ ] 新建 `adshare/models/stock_schemas.py` — 请求/响应模型
- [ ] 更新 `adshare/main.py` 注册路由（预留）

### Step 2：实现 stock_basic + trade_cal（1 天）
- [ ] `adshare/routers/stock_data.py` — `GET /stock_basic`
- [ ] `adshare/routers/stock_data.py` — `GET /trade_cal`
- [ ] 单元测试

### Step 3：实现 daily + weekly + monthly（1 天）
- [ ] `GET /daily` — 复用 kline + 计算涨跌幅 + 单位转换
- [ ] `GET /weekly` / `GET /monthly` — 同上，仅 period 不同
- [ ] 单元测试

### Step 4：实现 adj_factor + pro_bar（1.5 天）
- [ ] `GET /adj_factor` — 直接暴露 L3 adj_factor
- [ ] `GET /pro_bar` — 组装 daily + adj_factor + 复权 + 均线
- [ ] 单元测试

### Step 5：实现 suspend_d + 收尾（0.5 天）
- [ ] `GET /suspend_d` — 从 K-line 推导停牌
- [ ] 集成测试、文档补充

---

## 十、示例调用代码

```python
import requests

BASE = "http://localhost:8000"

# 1. 股票基础信息
r = requests.get(f"{BASE}/stock_basic", params={
    "list_status": "L",
    "fields": "ts_code,symbol,name,area,industry,list_date"
})
print(r.json()["data"]["items"][:3])

# 2. 交易日历
r = requests.get(f"{BASE}/trade_cal", params={
    "exchange": "SSE",
    "start_date": "20250101",
    "end_date": "20250131"
})
print(r.json()["data"]["items"])

# 3. 日线行情
r = requests.get(f"{BASE}/daily", params={
    "ts_code": "000001.SZ",
    "start_date": "20250601",
    "end_date": "20250610"
})
print(r.json()["data"]["items"])

# 4. 前复权行情
r = requests.get(f"{BASE}/pro_bar", params={
    "ts_code": "000001.SZ",
    "start_date": "20250601",
    "end_date": "20250610",
    "adj": "qfq",
    "ma": "5,10"
})
print(r.json()["data"]["items"])
```
