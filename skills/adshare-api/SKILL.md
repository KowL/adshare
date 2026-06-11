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
curl http://localhost:8000/login/status
```

### Market Data

```bash
# Code list
curl http://localhost:8000/market/codes

# K-line
curl "http://localhost:8000/market/kline?codes=000001.SZ&begin_date=20240101&end_date=20241231&period=day"

# Snapshot
curl "http://localhost:8000/market/snapshot?codes=000001.SZ"

# Stock basic info
curl "http://localhost:8000/market/stock/basic?codes=000001.SZ"
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

# Get K-line
r = requests.get(f"{base}/market/kline", params={
    "codes": "000001.SZ",
    "begin_date": "20240101",
    "end_date": "20241231",
    "period": "day"
}, headers=headers)
data = r.json()
print(f"Total bars: {data['count']}")
for bar in data["data"][:3]:
    print(f"  {bar['date']}: open={bar['open']}, close={bar['close']}")

# Get stock basic info
r = requests.get(f"{base}/market/stock/basic", params={"codes": "000001.SZ"})
basic = r.json()
print(f"Name: {basic['data'][0]['name']}, List date: {basic['data'][0]['list_date']}")
```

## TypeScript Example

```typescript
const base = "http://localhost:8000";
const headers = { "X-API-Key": "your-api-key" }; // if auth enabled

// Get K-line
const params = new URLSearchParams({
  codes: "000001.SZ",
  begin_date: "20240101",
  end_date: "20241231",
  period: "day",
});
const res = await fetch(`${base}/market/kline?${params}`, { headers });
const data = await res.json();
console.log(`Total bars: ${data.count}`);

// Get trading calendar
const cal = await fetch(`${base}/market/calendar?market=SH`).then(r => r.json());
console.log(`Trading days: ${cal.calendar.slice(0, 5).join(", ")}...`);
```

## Error Handling

| Status | Meaning | Action |
|--------|---------|--------|
| 401 | API Key missing | Check X-API-Key header |
| 403 | Invalid API Key | Verify key in .env |
| 500 | AmazingData not connected | Check /login/status, call /login |
| 503 | Redis disconnected | Check docker compose status |

## MCP Integration

adshare also exposes an MCP server at `/mcp` for AI Agent integration.
