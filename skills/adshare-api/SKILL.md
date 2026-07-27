---
title: adshare-api
version: 1.0.0
description: Connect to adshare data service for China A-share market data
author: adshare
tags: [finance, a-share, api, data]
---

# adshare-api

Connect to the **adshare** data middleware service for China A-share market data.

## When to Use

Use this skill when you need:
- Stock codes list, K-line data, snapshots
- Stock basic information
- Trading calendar
- Financial statements
- Any data that requires AmazingData SDK (Linux/amd64 only)

## Prerequisites

- adshare service running (default: http://localhost:8000)
- API Key (if auth_enabled)

## Configuration

Set in your `.env`:

```env
ADSHARE_URL=http://localhost:8000
ADSHARE_API_KEY=your-api-key
```

## API Endpoints

### Health & Status

```bash
curl http://localhost:8000/health
# Data-source session status is held by the worker service; the API
# returns 503 for /login* endpoints in API-only mode.
curl http://localhost:8000/login/status
```

### Market Data (Tushare Pro compatible)

```bash
# Stock basic info (POST /tushare with api_name dispatch)
curl -X POST http://localhost:8000/tushare \
  -H "Content-Type: application/json" \
  -d '{"api_name":"stock_basic","token":"your-api-key","params":{"ts_code":"000001.SZ"}}'

# Daily K-line
curl -X POST http://localhost:8000/tushare \
  -H "Content-Type: application/json" \
  -d '{"api_name":"daily","token":"your-api-key","params":{"ts_code":"000001.SZ","start_date":"20240101","end_date":"20241231"}}'

# Trading calendar
curl -X POST http://localhost:8000/tushare \
  -H "Content-Type: application/json" \
  -d '{"api_name":"trade_cal","token":"your-api-key","params":{"exchange":"SSE","start_date":"20240101","end_date":"20241231"}}'

# Limit-up / limit-down board stocks (limit_list_d)
curl -X POST http://localhost:8000/tushare \
  -H "Content-Type: application/json" \
  -d '{"api_name":"limit_list_d","token":"your-api-key","params":{"trade_date":"20240615","limit_type":"U"}}'
```

### Real-time Data

```bash
# Snapshot quote
curl "http://localhost:8000/realtime/quote/000001.SZ"

# Multi-code quotes
curl "http://localhost:8000/realtime/quotes?codes=000001.SZ,600000.SH"

# Real-time K-line
curl "http://localhost:8000/realtime/kline/000001.SZ?period=min5"
```

### Status

```bash
curl http://localhost:8000/status
curl http://localhost:8000/status/data-freshness
```

### Financial Data

```bash
# Balance sheet
curl "http://localhost:8000/financial/statement?codes=000001.SZ&statement_type=balance"

# Income statement
curl "http://localhost:8000/financial/statement?codes=000001.SZ&statement_type=income"

# Cash flow
curl "http://localhost:8000/financial/statement?codes=000001.SZ&statement_type=cashflow"

# Shareholder data
curl "http://localhost:8000/financial/shareholder?codes=000001.SZ"
```

## Python Example

```python
import requests

base = "http://localhost:8000"
headers = {"X-API-Key": "your-api-key"}  # if auth enabled

# Get daily K-line via the unified Tushare Pro entry point
r = requests.post(f"{base}/tushare", json={
    "api_name": "daily",
    "token": "your-api-key",
    "params": {
        "ts_code": "000001.SZ",
        "start_date": "20240101",
        "end_date": "20241231",
    },
}, headers=headers)
data = r.json()
items = data["data"]["items"]
print(f"Total bars: {len(items)}")
for bar in items[:3]:
    print(f"  {bar[0]}: open={bar[1]}, close={bar[2]}")

# Get stock basic info
r = requests.post(f"{base}/tushare", json={
    "api_name": "stock_basic",
    "token": "your-api-key",
    "params": {"ts_code": "000001.SZ"},
}, headers=headers)
basic = r.json()
print(basic["data"]["items"][0])
```

## TypeScript Example

```typescript
const base = "http://localhost:8000";
const headers = { "Content-Type": "application/json", "X-API-Key": "your-api-key" };

// Get daily K-line via the unified Tushare Pro entry point
const res = await fetch(`${base}/tushare`, {
  method: "POST",
  headers,
  body: JSON.stringify({
    api_name: "daily",
    token: "your-api-key",
    params: {
      ts_code: "000001.SZ",
      start_date: "20240101",
      end_date: "20241231",
    },
  }),
});
const data = await res.json();
console.log(`Total bars: ${data.data.items.length}`);

// Get trading calendar
const cal = await fetch(`${base}/tushare`, {
  method: "POST",
  headers,
  body: JSON.stringify({
    api_name: "trade_cal",
    token: "your-api-key",
    params: { exchange: "SSE", start_date: "20240101", end_date: "20240131" },
  }),
}).then(r => r.json());
console.log(`Trading days: ${cal.data.items.slice(0, 5).map(r => r[1]).join(", ")}...`);
```

## Error Handling

| Status | Meaning | Action |
|--------|---------|--------|
| 401 | API Key missing | Check X-API-Key header |
| 403 | Invalid API Key | Verify key in .env |
| 500 | Internal error | Check API service logs |
| 503 | Data source session held by worker / Redis disconnected | Check docker compose status |
