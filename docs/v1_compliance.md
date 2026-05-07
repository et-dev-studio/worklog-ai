# v1 Blueprint Compliance Matrix

| Blueprint area | Status | Implementation |
| --- | --- | --- |
| interactive connect selection | exact | `cli/main.py::connect` |
| human title governance | exact | `db/models.py` listener + `WorkstreamService.rename` |
| reflection scoring signals | exact | weighted short/no ws/no status/investigating/fixed |
| prompt runtime coverage | exact | reflection, summarize, grouping, categorize used |
| summary approval & audit | exact | decision/edit metadata logged in summary audit event |
| export formats | exact | markdown/json/terminal/all via `ExportFormat` |
| daemon lifecycle | exact | pid + heartbeat + start/stop/restart/status/degraded handling |
| advanced continuity matching | exact | overlap/tech/recency/prior/history/temporal weighted score |
