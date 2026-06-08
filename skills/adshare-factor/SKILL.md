---
title: adshare-factor
version: 1.0.0
description: Factor analysis and multi-factor synthesis for China A-share quant research via adshare
tags: [finance, factor-analysis, quant, multi-factor, a-share]
---

# adshare-factor

Factor analysis and multi-factor synthesis for China A-share quantitative research, powered by adshare service.

## When to Use

Use this skill when you need to:
- Analyze factor effectiveness (IC, IR, return)
- Run stratified backtests for factors
- Combine multiple factors into composite scores
- Detect and handle factor collinearity
- Build quantitative stock selection models

## Capabilities

### Preprocessing
- **MAD 去极值**: Median Absolute Deviation outlier removal
- **Z-Score 标准化**: Standardize factor values
- **中位数补空**: Fill missing values with median

### Analysis
- **IC 分析**: Spearman rank correlation between factor and forward returns
- **截面回归**: Cross-sectional regression (factor → returns)
- **分层回测**: Stratified backtest (group stocks by factor, track performance)

### Composite
- **共线性检测**: VIF and condition number
- **正交化**: Gram-Schmidt orthogonalization
- **加权合成**: Equal weight or custom weights

## API Usage

### Capabilities

```bash
curl http://localhost:8000/factor/capabilities
```

### Single Factor Analysis

```bash
curl "http://localhost:8000/factor/analyze?factor_name=momentum&stock_list=000001.SZ,000002.SZ&begin_date=20240101&end_date=20241231&group_num=5"
```

Parameters:
- `factor_name`: Factor name (ma5, ma10, momentum)
- `stock_list`: Comma-separated stock codes
- `begin_date`: Start date YYYYMMDD
- `end_date`: End date YYYYMMDD
- `group_num`: Number of stratification groups (default: 5)
- `ic_decay`: IC decay period (default: 20)

### Multi-Factor Composite

```bash
curl -X POST "http://localhost:8000/factor/composite" \
  -H "Content-Type: application/json" \
  -d '{
    "factor_names": ["momentum", "volatility", "size"],
    "stock_list": "000001.SZ,000002.SZ",
    "begin_date": 20240101,
    "end_date": 20241231,
    "weight_method": "equal",
    "use_orthogonal": true
  }'
```

### Python Example

```python
import requests

base = "http://localhost:8000"

# Analyze momentum factor
r = requests.get(f"{base}/factor/analyze", params={
    "factor_name": "momentum",
    "stock_list": "000001.SZ,000002.SZ,600519.SH",
    "begin_date": "20240101",
    "end_date": "20241231",
    "group_num": 5
})
result = r.json()
print(f"IC Mean: {result['ic_mean']:.4f}")
print(f"Annual Return: {result['annual_return']:.2%}")
print(f"Sharpe: {result['sharpe_ratio']:.2f}")
print(f"Max Drawdown: {result['max_drawdown']:.2%}")
```

## Factor Quality Criteria

| Metric | Good | Excellent |
|--------|------|-----------|
| IC Mean | > 0.03 | > 0.05 |
| IC IR | > 0.3 | > 0.5 |
| Annual Return | > 5% | > 10% |
| Sharpe Ratio | > 0.5 | > 1.0 |
| Max Drawdown | < 15% | < 10% |

## Multi-Factor Workflow

1. **Select candidate factors** (momentum, value, quality, etc.)
2. **Preprocess** each factor (MAD → Z-Score → fillna)
3. **Detect collinearity** (VIF > 10 or condition number > 30)
4. **Orthogonalize** if needed
5. **Assign weights** (equal, IC-weighted, or custom)
6. **Composite** into single score
7. **Backtest** composite factor

## Notes

- Factor data computed from K-line via adshare
- All calculations in pandas/numpy (no AmazingData dependency for computation)
- Results cached in Redis
