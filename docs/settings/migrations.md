# After an update: old setting names

A labpipe release sometimes renames a setting, moves it to another section, drops it, or changes what its value means. Every such change comes with a **migration**: labpipe knows how to turn the old spelling of your settings files into the new one.

```bash
labpipe migrate                 # shows what would change, file by file, as a diff; changes nothing
labpipe migrate --apply         # asks, then changes the files (add --yes to skip the question)
```

- **Which files**: the ones you own: `config/local.yaml`, `lab.yaml`, `samples.yaml`, `sample_ids.yaml`, `ignore.yaml`, `config/measurements/**/*.yaml`, `labpipe.yaml`, and your own methods' defaults `lab/methods/*/config.yaml`. labpipe's defaults come with the code and are never touched.
- **Backup**: before a file is changed it is copied to `backups/migrate-<UTC time>/` in the project (same relative path, e.g. `backups/migrate-20261020T101500Z/config/lab.yaml`); the command says where. An existing backup is never overwritten. To undo, copy the file back.
- **Comments are kept** when a key is renamed or a plain value is changed: only the key's name or the value changes, in place. Where that is not possible (a setting moved to another section, a value that turns into a list) the file is rewritten from its data and **the comments in that file are lost**; `labpipe migrate` says so per file ("comments are not kept") before you agree. The backup has them.
- **Nothing is lost if you wait**: for one release the old names are still understood, with a warning on screen ("`'plots.dpi'` is now `'plots.resolution'` (labpipe v0.3.0); run labpipe migrate"). Names that are neither old nor new are still refused: typos fail loudly. After that release the old names stop working, so migrate soon. `labpipe doctor` shows an item `settings_migrations` while a file still uses old names (a warning, not blocking).
- **Results do not change by migrating.** If a migrated setting means something else, the entries it concerns may become out of date (`labpipe status`); redo them only when you want (`labpipe requeue --stale`).

Your agent shows you `labpipe migrate` and its diff and applies it only after your OK.

## You say → what happens

| You say | Do |
|---|---|
| "labpipe warns that a setting has a new name" | `labpipe migrate`, read the diff, `labpipe migrate --apply` |
| "I want to see what an update would change in my settings" | `labpipe migrate` (changes nothing) |
| "undo the migration" | copy the files from `backups/migrate-<time>/` back into the project |

For developers: a release that renames, moves or drops a settings key adds a `Migration` to `migrations.MIGRATIONS` (`src/labpipe/migrations.py`) with a test, and a line under "Settings migrated" in `CHANGELOG.md`; CI (`scripts/check_settings_migrations.py`) refuses the change otherwise. See `docs/ARCHITECTURE.md` → Settings migrations and versioned checks.
