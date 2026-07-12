from adshare.historical.sync import sync_kline_daily
from adshare.historical.warehouse import get_warehouse
from adshare.core.config import get_settings
from datetime import datetime

settings = get_settings()
wh = get_warehouse(settings)
wh.refresh_views()
row = wh.connection.execute('SELECT MAX(date) FROM v_kline_day').fetchone()
last_date = row[0] if row and row[0] else 20200101
end_date = int(datetime.now().strftime('%Y%m%d'))
result = sync_kline_daily(from_date=int(last_date), to_date=end_date)
print(f'Incremental sync: {last_date} -> {end_date}')
print(f'succeeded={result.succeeded} failed={result.failed} rows={result.rows} duration={result.duration:.2f}s')
