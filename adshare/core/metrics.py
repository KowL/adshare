"""Prometheus metrics for adshare."""

from prometheus_client import Counter, Histogram, Info, generate_latest

# Service info
SERVICE_INFO = Info("adshare", "Adshare service information")

# Request metrics
REQUEST_COUNT = Counter(
    "adshare_requests_total",
    "Total requests",
    ["method", "endpoint", "status"],
)

REQUEST_DURATION = Histogram(
    "adshare_request_duration_seconds",
    "Request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Data source metrics
AD_REQUEST_COUNT = Counter(
    "adshare_amazingdata_requests_total",
    "AmazingData requests",
    ["method", "status"],
)

AD_REQUEST_DURATION = Histogram(
    "adshare_amazingdata_request_duration_seconds",
    "AmazingData request duration",
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

def get_metrics() -> bytes:
    """Generate Prometheus metrics output."""
    return generate_latest()
