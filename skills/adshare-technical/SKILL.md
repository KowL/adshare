---
title: adshare-technical
version: 1.0.0
description: Technical analysis indicators for China A-share stocks via adshare
tags: [finance, technical-analysis, indicators, a-share]
---

# adshare-technical

Technical analysis indicators for China A-share stocks, powered by adshare service.

## When to Use

Use this skill when you need to:
- Calculate technical indicators (MACD, KDJ, RSI, BOLL, etc.)
- Analyze stock price trends and momentum
- Generate buy/sell signals from technical patterns
- Compare multiple indicators for a stock

## Available Indicators (56 total)

### Overbought/Oversold (14)
KDJ, RSI, WR, CCI, ROC, MTM, BIAS, SKDJ, MFI, OSC, UDL, ACCER, RCCD, MARSI

### Trend (14)
MACD, DMI, DMA, TRIX, ARBR, EMV, DPO, VHF, CHO, DBCD, DDI, JS, QACD, UOS

### Energy (5)
CR, PSY, MASS, PCNT, WAD

### Volume (9)
OBV, VOLMA, VOSC, VRSI, VSTD, VR, WVAD, TAPI, AMV

### Moving Average (4)
MA, BBI, PBX, EXPMA

### Path (6)
BOLL, ENE, CDP, MIKE, SAR, ATR

### Other (4)
XS, ASI, BBIBOLL, AMO

## API Usage

### List All Indicators

```bash
curl http://localhost:8000/technical/indicators
```

### Analyze Single Indicator

```bash
curl "http://localhost:8000/technical/analyze?code=000001.SZ&indicator=MACD&period=day&begin_date=20240101&end_date=20241231"
```

Parameters:
- `code`: Stock code (e.g., 000001.SZ)
- `indicator`: Indicator name (e.g., MACD, KDJ, RSI)
- `period`: day/week/month (default: day)
- `begin_date`: Start date YYYYMMDD
- `end_date`: End date YYYYMMDD

### Python Example

```python
import requests

base = "http://localhost:8000"

# Get MACD for 平安银行
r = requests.get(f"{base}/technical/analyze", params={
    "code": "000001.SZ",
    "indicator": "MACD",
    "period": "day",
    "begin_date": "20240101",
    "end_date": "20241231"
})
macd = r.json()
print(f"DIF: {macd['DIF'][-1]}, DEA: {macd['DEA'][-1]}, MACD: {macd['MACD'][-1]}")
```

## Signal Interpretation

| Indicator | Buy Signal | Sell Signal |
|-----------|-----------|-------------|
| MACD | DIF crosses above DEA | DIF crosses below DEA |
| KDJ | K crosses above D, J < 20 | K crosses below D, J > 80 |
| RSI | RSI < 30 (oversold) | RSI > 70 (overbought) |
| BOLL | Price touches lower band | Price touches upper band |
| DMI | PDI crosses above MDI | MDI crosses above PDI |

## Notes

- All indicators calculated locally in pandas/numpy (no AmazingData dependency for computation)
- K-line data fetched from AmazingData via adshare
- Results cached in Redis for performance
