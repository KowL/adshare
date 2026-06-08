---
title: adshare-fundamental
version: 1.0.0
description: Fundamental analysis factors for China A-share stocks via adshare
tags: [finance, fundamental-analysis, factors, a-share]
---

# adshare-fundamental

Fundamental analysis factors for China A-share stocks, powered by adshare service.

## When to Use

Use this skill when you need to:
- Evaluate company financial health (ROE, ROA, profit margins)
- Assess growth potential (revenue growth, earnings growth)
- Analyze valuation (PE, PB, EV/EBITDA)
- Check safety metrics (debt ratios, interest coverage)
- Screen stocks by fundamental criteria

## Available Factors (90 total)

### Profitability (9)
全部资产现金回收率TTM/变动, 资产回报率TTM/变动, 净资产收益率TTM/变动, 资本回报率TTM/变动, 税费负担占净资产比

### Growth (21)
营业收入增速, 每股盈利/增速, 扣非净利润增速_TTM同比, 净利润增速_单季度同比/环比/TTM同比, etc.

### Efficiency (15)
总资产周转率, 应收账款周转率, 存货周转率, 现金转换周期, etc.

### Earnings Quality (8)
营业利润占比, 经营活动净收益占比, 所得税/利润总额, etc.

### Safety (14)
资产负债率, 流动比率, 速动比率, 利息保障倍数, etc.

### Governance (2)
管理层薪酬/营业总收入, 在职员工/营业总收入

### Valuation (12)
市盈率, 市净率, 市现率, 市销率, EV/EBITDA, etc.

### Shareholder (4)
户均持股, 机构持股比例, etc.

### Size (5)
对数总资产, 对数市值, etc.

## API Usage

### List All Factors

```bash
curl http://localhost:8000/fundamental/factors
```

### Analyze Single Factor

```bash
curl "http://localhost:8000/fundamental/analyze?code=000001.SZ&factor=ROE_TTM"
```

Parameters:
- `code`: Stock code (e.g., 000001.SZ)
- `factor`: Factor name (e.g., ROE_TTM, PE_TTM, 营业收入增速)

### Python Example

```python
import requests

base = "http://localhost:8000"

# Get ROE_TTM for 平安银行
r = requests.get(f"{base}/fundamental/analyze", params={
    "code": "000001.SZ",
    "factor": "ROE_TTM"
})
roe = r.json()
print(f"ROE_TTM: {roe['value']}")
```

## Factor Interpretation

| Factor | Good | Bad |
|--------|------|-----|
| ROE_TTM | > 15% | < 5% |
| PE | 10-30 | > 50 or < 0 |
| PB | 1-3 | > 5 |
| 资产负债率 | < 60% | > 80% |
| 营业收入增速 | > 20% | < 0% |
| 流动比率 | > 1.5 | < 1 |

## Notes

- Financial data from AmazingData via adshare
- TTM (Trailing 12 Months) calculated locally
- Quarterly data with automatic cumulative-to-single-quarter conversion
