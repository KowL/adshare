"""Single source of truth for realtime Redis keys and Pub/Sub channels.

Shared by both processes:

* the **worker** publisher (:mod:`amazingdata_worker.realtime_publisher`)
  writes quote/kline/index payloads and publishes broadcast messages;
* the **API** process reads the same keys for REST queries
  (:mod:`adshare.routers.realtime`) and subscribes to the same channels
  for WS/SSE broadcast (:mod:`adshare.services.realtime_broadcast`).
"""

# Redis keys holding the latest payloads (queried by the REST API).
REALTIME_QUOTE_KEY = "realtime:quote"
REALTIME_KLINE_KEY = "realtime:kline"
REALTIME_INDEX_KEY = "realtime:index"

# Redis Pub/Sub channels consumed by the API-side broadcast service.
CHANNEL_QUOTE = "adshare:realtime:quote"
CHANNEL_INDEX = "adshare:realtime:index"
CHANNEL_KLINE_PREFIX = "adshare:realtime:kline:"
