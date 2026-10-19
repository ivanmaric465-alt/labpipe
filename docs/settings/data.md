# Data: folder, recognised files, sample IDs

labpipe reads your instrument files from your **data folder** (through a read-only view, `docs/SAFETY.md`), decides which method each file belongs to, and takes the sample ID from the file name.

## Settings

| What | Where | Default |
|---|---|---|
| Data folder(s) | `config/local.yaml` → `raw_roots` (`id`, `path`, `source`) and `protected_paths` | none: `labpipe setup` asks |
| Which files a method reads | `methods.yaml` → `<method>.patterns`, `ignore` | per method |
| Files to leave alone | `config/ignore.yaml` → `ignore:` (globs relative to the data folder) | none |
| Never measurements (system files) | `config/pipeline.yaml` → `system_files` | `.*`, `Thumbs.db`, `~$*`, … |
| Wait before analysing a new file | `config/local.yaml` → `automation.settle_minutes` | 3 (watcher), 10 (`pipeline.yaml`) |
| Sample ID from the file name | `config/pipeline.yaml` → `sample_id.separators`, `pattern` | stem up to `_` or space; IDs like `S-9` |
| Sample ID for one file | `config/sample_ids.yaml` (keyed by path relative to the data folder) | none |

```bash
labpipe inventory        # what is recognised, what is not, names without a sample ID
labpipe scan             # apply the changes
```

## You say → setting

| You say | Setting |
|---|---|
| "my data is now in /mnt/lab/xrd" | `raw_roots[].path` (keep its `id`, the manifest uses it); `labpipe readonly-view --source PATH` prints the commands for the read-only view |
| "ignore the TEM folder" / "those .docx are reports" | `config/ignore.yaml`: `ignore: ["TEM/**", "**/*.docx"]` with a `#` comment why |
| "these .txt are XRD exports" | `methods.yaml` → `xrd.patterns` (a code-level change: test with `labpipe methods check xrd --example FILE`) |
| "our samples are called HX12, no dash" | `sample_id.pattern`, then `labpipe inventory` until the examples look right |
| "`S-9 old.xrdml` is really S-10" | `config/sample_ids.yaml`: `"XRD/S-9 old.xrdml": S-10` |

## After a change

`labpipe scan` picks it up. A file waiting for its sample ID (`needs_review`) is analysed in the next pass. A file already analysed under another ID: rerun it (`labpipe <method> analyze --entry ID --rerun`, code), then its notes are rewritten (AI).
