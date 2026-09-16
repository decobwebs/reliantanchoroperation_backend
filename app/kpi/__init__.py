"""KPI engine.

Self-contained on purpose: catalog, model, schemas, service and router all
live in this package, and the only wiring outside it is one include_router
line in app/main.py. The feature can be reviewed, disabled or lifted out
without touching the rest of the API.

The pre-existing app/services/kpi_service.py (vessel stage timings, used by
the operation KPI tab) is left exactly as it was and is reused from here
rather than duplicated.
"""
