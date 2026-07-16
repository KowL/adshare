"""Run reference data sync inside the worker container."""
from amazingdata.batch import (
    sync_financial,
    sync_shareholder,
    sync_index_component,
)
from amazingdata.adapters.amazingdata import get_adapter
from adshare.core.config import get_settings
from adshare.historical.warehouse import get_warehouse

adapter = get_adapter()
settings = get_settings()
warehouse = get_warehouse(settings)

with open("/tmp/ref_sync.log", "w") as f:
    for stmt in ("balance", "income", "cashflow"):
        print(f"=== financial {stmt} ===", file=f, flush=True)
        r = sync_financial(
            stmt,
            batch_size=50,
            settings=settings,
            warehouse=warehouse,
            adapter=adapter,
        )
        print(r, file=f, flush=True)
    print("=== shareholder ===", file=f, flush=True)
    r = sync_shareholder(
        batch_size=50,
        settings=settings,
        warehouse=warehouse,
        adapter=adapter,
    )
    print(r, file=f, flush=True)
    print("=== index component ===", file=f, flush=True)
    r = sync_index_component(
        settings=settings,
        warehouse=warehouse,
        adapter=adapter,
    )
    print(r, file=f, flush=True)
