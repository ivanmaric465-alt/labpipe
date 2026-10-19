---
name: labpipe-pass
description: Run one labpipe pipeline pass (scan the raw data, analyse new measurements, write and verify notes, rebuild the report). Use for "process the new data", "what's new?", or a scheduled run.
---

Run one pipeline pass exactly as the supervisor role describes it in
`agents/supervisor.md`: `labpipe doctor`, `labpipe scan`, `labpipe pending`
(delegate each row to the analyst agent named in its `subagent` field; rows with no
subagent: `labpipe analyze --pending --method <method>`), `labpipe notes todo`,
`labpipe review`, `labpipe report`, then the run summary in `logs/runs/`.

The rules in `AGENTS.md` bind you: only `labpipe ...` commands, no numbers of your
own, raw data read-only, nothing deleted.
