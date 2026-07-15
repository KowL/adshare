# adshare

AmazingData shared data service — A financial data middleware for China A-share markets.

## Overview

adshare is a standalone data service that wraps the AmazingData SDK (Linux/amd64 only) and exposes financial data via HTTP REST API and MCP protocol. It enables multiple projects and AI agents to share a single data source with unified authentication, caching, rate limiting, and monitoring.

## Features

- **Tushare Compatible**: Drop-in `tushare.py` adapter and `/tushare/*` endpoints for existing tushare-based projects
- **Market Data**: K-line, snapshot, code list, stock basic info, trading calendar
- **Financial Data**: Balance sheet, income statement, cash flow, shareholder data
- **Technical Analysis**: 56 indicators (MACD, KDJ, RSI, BOLL, DMI, etc.)
- **Fundamental Analysis**: 90 factors (ROE, PE, growth, safety, valuation, etc.)
- **Factor Analysis**: IC analysis, stratified backtest, multi-factor composite
- **Real-time State**: Redis for subscription/snapshot short-lived market data
- **Historical Warehouse**: Local Parquet + DuckDB written by scheduled sync jobs
- **Monitoring**: Prometheus metrics at `/metrics`
- **Rate Limiting**: SlowAPI with configurable limits
- **Auth**: API Key authentication (optional)
- **MCP**: Model Context Protocol server for AI Agent integration

## Quick Start

### Requirements

- Docker + Docker Compose
- Linux x86_64 server (for AmazingData SDK)
- Python 3.11+ (for local development)

### Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

```env
# AmazingData credentials
AD_USERNAME=your_username
AD_PASSWORD=your_password
AD_HOST=amazingdata.example.com
AD_PORT=8600

# API Key (optional, set AUTH_ENABLED=true to enable)
ADSHARE_API_KEY=your-secret-api-key

# Redis (default via docker compose)
REDIS_HOST=redis
REDIS_PORT=6379
```

### Deploy

```bash
# On x86_64 Linux server
scp -r adshare/ server:/opt/
ssh server "cd /opt/adshare && bash scripts/deploy.sh"
```

Or manually:

```bash
docker compose up -d
```

### Verify

```bash
curl http://localhost:8000/health
curl http://localhost:8000/technical/indicators
curl http://localhost:8000/fundamental/factors
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |
| `/market/codes` | GET | Stock code list |
| `/market/kline` | GET | K-line data |
| `/market/snapshot` | GET | Snapshot data |
| `/market/stock/basic` | GET | Stock basic info |
| `/financial/statement` | GET | Financial statements |
| `/financial/shareholder` | GET | Shareholder data |
| `/technical/indicators` | GET | List all indicators |
| `/technical/analyze` | GET | Calculate indicator |
| `/fundamental/factors` | GET | List all factors |
| `/fundamental/analyze` | GET | Calculate factor |
| `/factor/capabilities` | GET | Factor analysis capabilities |
| `/factor/analyze` | GET | Run factor analysis |
| `/factor/composite` | POST | Composite multiple factors |

| `/tushare/stock/daily` | GET/POST | Tushare Pro compatible daily K-line |
| `/tushare/stock/weekly` | GET/POST | Tushare Pro compatible weekly K-line |
| `/tushare/stock/monthly` | GET/POST | Tushare Pro compatible monthly K-line |
| `/tushare/stock/stock_basic` | GET/POST | Tushare Pro compatible stock basic |
| `/tushare/stock/trade_cal` | GET/POST | Tushare Pro compatible trading calendar |
| `/tushare/stock/adj_factor` | GET/POST | Tushare Pro compatible adjustment factor |
| `/tushare/stock/suspend_d` | GET/POST | Tushare Pro compatible suspension info |
| `/tushare/stock/limit_list` | GET/POST | Tushare Pro compatible limit-up/down list |

See `/docs` for full OpenAPI documentation.

## Tushare Compatibility

adshare provides a tushare Pro protocol compatible layer. Existing projects using `import tushare as ts` can switch to adshare by copying the project-root `tushare.py` file and pointing `pro_api()` at the adshare server:

```python
import tushare as ts

pro = ts.pro_api("http://localhost:8000/tushare")
df = pro.daily(ts_code="000001.SZ", start_date="20240101", end_date="20240131")
```

See [docs/tushare-migration.md](docs/tushare-migration.md) for details.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────┐
│   Client    │────▶│  adshare    │────▶│  AmazingData    │
│  (Vibe-     │     │  (FastAPI)  │     │  SDK (Linux/    │
│  Trading,   │◀────│             │◀────│  amd64 only)    │
│  ruo-cli)   │     │  - Redis RT │     │                 │
│             │     │  - Warehouse│     │                 │
└─────────────┘     └─────────────┘     └─────────────────┘
```

## Project Structure

```
adshare/
├── adshare/              # Python package
│   ├── main.py           # FastAPI entry
│   ├── core/             # Config, cache, auth, metrics
│   ├── adapters/         # AmazingData SDK adapter
│   ├── engines/          # Technical, fundamental, factor
│   ├── routers/          # API endpoints
│   ├── models/           # Pydantic schemas
│   └── mcp/              # MCP server
├── tests/                # Pytest test suite
├── skills/               # 4 Skill files for AI agents
├── scripts/              # Deployment scripts
├── config/               # Settings YAML
├── docker-compose.yml    # Docker orchestration
├── Dockerfile            # Container image
└── pyproject.toml        # Dependencies
```

## Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run locally (without Docker)
uvicorn adshare.main:app --reload
```

## License

MIT
