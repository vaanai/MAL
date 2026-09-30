"""Runners for the MAL Console job templates (docs/console-plan.md §9 item 2).

Each submodule exposes `resolve_data_blocks(params) -> list[dict]` (the hours the
template would read, for tools.mal_catalog.check_read) and `run(params) -> dict`
(the actual scoring; see docs/contracts/job-templates.md for wiring status). Both
are pure functions over a params dict -- no data is touched by resolve_data_blocks,
and tools/mal_job.py calls it before run() is ever reached.
"""
