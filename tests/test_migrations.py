"""Settings migrations: plan, apply, comments kept, old keys for one release, the command, doctor,
and the CI check. (Host-side: no numpy needed.)"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from labpipe import cli, doctor, migrations, registry, safety, settings
from labpipe.migrations import Migration, move, remove, rename, transform

REPO = Path(__file__).resolve().parents[1]

LAB = """\
# The lab's preferences.
plots:
  font_size: 9        # small: the journal asks for it
  dpi: 600            # for the figures
  palette: okabe-ito

xrd:
  # why: two phases only
  background: {order: 3}
  note: "dpi: 5 # not a key"
report:
  group_by: method
"""

DPI = Migration("0.3.0-plots-dpi", "v0.3.0", "plots.dpi is now plots.resolution",
                ("config/lab.yaml",), operations=[rename("plots.dpi", "plots.resolution")])


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch):
    monkeypatch.setattr(migrations, "_WARNED", set())


@pytest.fixture
def project(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "lab.yaml").write_text(LAB, encoding="utf-8")
    return tmp_path


def _only(items):
    assert len(items) == 1
    return items[0]


# ---- the plan: comments stay ------------------------------------------------------------

def test_rename_edits_only_the_key_and_keeps_every_comment(project):
    item = _only(migrations.plan(project, [DPI]))
    assert item["file"] == "config/lab.yaml" and item["comments_kept"] is True
    assert item["after"] == LAB.replace("  dpi: 600 ", "  resolution: 600 ")
    assert item["migrations"] == [{"id": "0.3.0-plots-dpi", "release": "v0.3.0",
                                   "description": "plots.dpi is now plots.resolution"}]
    assert "-  dpi: 600" in item["diff"] and "+  resolution: 600" in item["diff"]
    assert yaml.safe_load(item["after"])["plots"]["resolution"] == 600


def test_nothing_pending_when_the_old_key_is_absent(project):
    (project / "config" / "lab.yaml").write_text("plots:\n  resolution: 600\n", encoding="utf-8")
    assert migrations.plan(project, [DPI]) == []
    assert migrations.plan(project, []) == []


def test_rename_does_not_touch_lookalikes(project):
    """`dpi:` in a string, a list item, a block text and another section is not the key."""
    text = ("plots:\n  note: |\n    dpi: 1\n  dpi: 2\n  items:\n    - dpi: 3\n"
            "xrd:\n  dpi: 4\n")
    (project / "config" / "lab.yaml").write_text(text, encoding="utf-8")
    after = _only(migrations.plan(project, [DPI]))["after"]
    assert after == text.replace("  dpi: 2", "  resolution: 2")


def test_rename_with_a_wildcard_and_quoted_key_and_crlf(project):
    text = 'samples:\r\n  S-1:\r\n    xrd: {a: 1}\r\n    "old": 5  # keep\r\n  S-2:\r\n    old: 6\r\n'
    (project / "config" / "samples.yaml").write_text(text, encoding="utf-8", newline="")
    migration = Migration("m", "v0.3.0", "d", ("config/samples.yaml",),
                          operations=[rename("samples.*.old", "samples.*.new")])
    item = _only(migrations.plan(project, [migration]))
    assert item["after"] == text.replace('"old"', '"new"').replace("old:", "new:")
    assert item["comments_kept"] is True and "\r\n" in item["after"]


def test_transform_changes_only_the_value_and_keeps_the_comment_column(project):
    migration = Migration("m", "v0.3.0", "plots.font_size is in points of the figure",
                          ("config/lab.yaml",), old_keys=("plots.font_size",),
                          operations=[transform("plots.font_size",
                                                lambda v: v if v >= 20 else v * 10,
                                                "is now in tenths of a point")])
    item = _only(migrations.plan(project, [migration]))
    assert item["after"] == LAB.replace("font_size: 9        #", "font_size: 90       #")
    assert item["comments_kept"] is True
    # applying it again changes nothing: the function returns a new value as it is
    (project / "config" / "lab.yaml").write_text(item["after"], encoding="utf-8")
    assert migrations.plan(project, [migration]) == []


def test_transform_keeps_the_quote_style_and_falls_back_for_a_section(project):
    (project / "config" / "lab.yaml").write_text('xrd:\n  unit: "A"\n  other: x\n',
                                                 encoding="utf-8")
    to_nm = Migration("m", "v0.3.0", "d", ("config/lab.yaml",), operations=[
        transform("xrd.unit", lambda v: "nm" if v == "A" else v, "now nm")])
    assert _only(migrations.plan(project, [to_nm]))["after"] == 'xrd:\n  unit: "nm"\n  other: x\n'
    (project / "config" / "lab.yaml").write_text("xrd:\n  unit: [a]  # list\n", encoding="utf-8")
    listed = Migration("m", "v0.3.0", "d", ("config/lab.yaml",), operations=[
        transform("xrd.unit", lambda v: v + ["b"], "now with b")])
    item = _only(migrations.plan(project, [listed]))
    assert yaml.safe_load(item["after"]) == {"xrd": {"unit": ["a", "b"]}}
    assert item["comments_kept"] is False       # done on the data, the comment is gone


def test_move_falls_back_to_the_data_and_says_the_comments_are_lost(project):
    migration = Migration("0.3.0-move", "v0.3.0", "report.group_by moved to plots",
                          ("config/lab.yaml",), operations=[move("report.group_by",
                                                                 "plots.group_by")])
    item = _only(migrations.plan(project, [migration]))
    assert item["comments_kept"] is False
    data = yaml.safe_load(item["after"])
    assert data["plots"]["group_by"] == "method" and "report" not in data   # no empty section
    assert data["xrd"]["note"] == "dpi: 5 # not a key"
    assert "#" not in item["after"].replace("# not a key", "")


def test_move_without_comments_loses_nothing(project):
    (project / "config" / "lab.yaml").write_text("report:\n  group_by: method\n", encoding="utf-8")
    migration = Migration("m", "v0.3.0", "d", ("config/lab.yaml",),
                          operations=[move("report.group_by", "plots.group_by")])
    assert _only(migrations.plan(project, [migration]))["comments_kept"] is True


def test_remove_deletes_the_key_with_what_it_holds(project):
    text = "plots:\n  dpi: 600   # old\n  font_size: 9\nxrd:\n  old:\n    a: 1\n    b: [2]\n\n  keep: 1\n"
    (project / "config" / "lab.yaml").write_text(text, encoding="utf-8")
    migration = Migration("m", "v0.3.0", "d", ("config/lab.yaml",),
                          operations=[remove("plots.dpi"), remove("xrd.old", "gone")])
    item = _only(migrations.plan(project, [migration]))
    assert item["after"] == "plots:\n  font_size: 9\nxrd:\n\n  keep: 1\n"
    assert item["comments_kept"] is True


def test_when_both_names_are_there_the_new_one_wins(project):
    (project / "config" / "lab.yaml").write_text("plots:\n  dpi: 1\n  resolution: 2\n",
                                                 encoding="utf-8")
    item = _only(migrations.plan(project, [DPI]))
    assert yaml.safe_load(item["after"]) == {"plots": {"resolution": 2}}


def test_several_migrations_and_files_and_globs(project):
    folder = project / "config" / "measurements" / "xrd"
    folder.mkdir(parents=True)
    (folder / "S-1_a.yaml").write_text("note: x  # why\nbackground: {order: 3}\n", encoding="utf-8")
    (folder / "S-1_b.yaml").write_text("background: {degree: 3}\n", encoding="utf-8")
    ms = migrations.method_setting("0.3.0-order", "v0.3.0", "background.order is now degree",
                                   "xrd", rename("background.order", "background.degree"))
    assert [m.id for m in ms] == ["0.3.0-order", "0.3.0-order-samples", "0.3.0-order-measurements"]
    assert ms[0].old_keys == ("xrd.background.order",)
    items = migrations.plan(project, [*ms, DPI])
    assert [i["file"] for i in items] == ["config/lab.yaml", "config/measurements/xrd/S-1_a.yaml"]
    assert "degree: 3}\n" in items[1]["after"] and "# why" in items[1]["after"]
    assert "degree" in items[0]["after"] and "resolution" in items[0]["after"]
    assert [m["id"] for m in items[0]["migrations"]] == ["0.3.0-order", "0.3.0-plots-dpi"]


def test_method_setting_reaches_samples_and_presets(project):
    (project / "config" / "samples.yaml").write_text(
        "presets:\n  p:\n    xrd: {background: {order: 2}}\nsamples:\n  S-1:\n    xrd: {background: {order: 4}}\n",
        encoding="utf-8")
    ms = migrations.method_setting("m", "v0.3.0", "d", "xrd",
                                   rename("background.order", "background.degree"))
    after = yaml.safe_load(_only(migrations.plan(project, ms[1:2]))["after"])
    assert after["presets"]["p"]["xrd"]["background"] == {"degree": 2}
    assert after["samples"]["S-1"]["xrd"]["background"] == {"degree": 4}


def test_keys_inside_one_line_mappings_are_edited_in_place(project):
    text = ("samples:\n  S-1: {presets: [p], xrd: {background: {order: 3, n: 2}}}  # why\n"
            "  S-2: {xrd: {background: {order: \"4\"}, other: 1}}\n")
    (project / "config" / "samples.yaml").write_text(text, encoding="utf-8")
    ms = migrations.method_setting("o", "v0.3.0", "d", "xrd",
                                   rename("background.order", "background.degree"))
    item = _only(migrations.plan(project, ms[1:2]))
    assert item["after"] == text.replace("order", "degree") and item["comments_kept"] is True
    up = Migration("u", "v0.3.0", "d", ("config/samples.yaml",), operations=[
        transform("samples.*.xrd.background.n", lambda v: v if v >= 10 else v * 10, "x10")])
    assert _only(migrations.plan(project, [up]))["after"] == text.replace("n: 2", "n: 20")


def test_the_example_files_survive_a_rename_with_their_comments():
    text = (REPO / "config" / "samples.example.yaml").read_text(encoding="utf-8")
    migration = Migration("m", "v0.3.0", "d", ("config/samples.yaml",), operations=[
        rename("presets.*.xrd.broad_components_deg", "presets.*.xrd.broad_deg")])
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "config").mkdir()
        (Path(folder) / "config" / "samples.yaml").write_text(text, encoding="utf-8")
        item = _only(migrations.plan(Path(folder), [migration]))
    assert item["after"] == text.replace("broad_components_deg", "broad_deg")
    assert item["comments_kept"] is True


def test_bad_operations_are_refused():
    with pytest.raises(ValueError):
        rename("plots.dpi", "report.dpi")
    with pytest.raises(ValueError):
        move("a.b", "c.*")


def test_files_that_are_not_mappings_or_not_yaml_are_skipped(project):
    (project / "config" / "lab.yaml").write_text("[1, 2]\n", encoding="utf-8")
    assert migrations.plan(project, [DPI]) == []
    (project / "config" / "lab.yaml").write_text("a: [unclosed\n", encoding="utf-8")
    assert migrations.plan(project, [DPI]) == []


# ---- apply ---------------------------------------------------------------------------

def test_apply_backs_up_then_writes(project):
    planned = migrations.plan(project, [DPI])
    backup = project / "backups" / "migrate-1"
    assert migrations.apply(project, planned, backup) == ["config/lab.yaml"]
    assert (backup / "config" / "lab.yaml").read_text(encoding="utf-8") == LAB
    assert (project / "config" / "lab.yaml").read_text(encoding="utf-8") == planned[0]["after"]
    assert migrations.plan(project, [DPI]) == []


def test_apply_refuses_when_a_file_changed_since_the_plan_and_writes_nothing(project):
    (project / "config" / "samples.yaml").write_text("samples:\n  S-1:\n    xrd: {old: 1}\n",
                                                     encoding="utf-8")
    other = Migration("o", "v0.3.0", "d", ("config/samples.yaml",),
                      operations=[rename("samples.*.xrd.old", "samples.*.xrd.new")])
    planned = migrations.plan(project, [DPI, other])
    assert len(planned) == 2
    (project / "config" / "samples.yaml").write_text("samples: {}\n", encoding="utf-8")
    with pytest.raises(migrations.MigrationError, match="changed since the plan"):
        migrations.apply(project, planned, project / "backups" / "b")
    assert (project / "config" / "lab.yaml").read_text(encoding="utf-8") == LAB
    assert not (project / "backups").exists()


def test_apply_never_overwrites_a_backup(project):
    planned = migrations.plan(project, [DPI])
    backup = project / "backups" / "b"
    (backup / "config").mkdir(parents=True)
    (backup / "config" / "lab.yaml").write_text("older backup", encoding="utf-8")
    with pytest.raises(migrations.MigrationError, match="never overwritten"):
        migrations.apply(project, planned, backup)
    assert (backup / "config" / "lab.yaml").read_text(encoding="utf-8") == "older backup"
    assert (project / "config" / "lab.yaml").read_text(encoding="utf-8") == LAB


# ---- old keys keep working for one release -------------------------------------------

def test_upgrade_in_memory_understands_old_names_and_warns():
    data = {"plots": {"dpi": 600, "font_size": 9}}
    new, warnings = migrations.upgrade_in_memory("config/lab.yaml", data, [DPI])
    assert new == {"plots": {"resolution": 600, "font_size": 9}}
    assert data == {"plots": {"dpi": 600, "font_size": 9}}         # the input is not changed
    assert warnings == ["config/lab.yaml: 'plots.dpi' is now 'plots.resolution' "
                        "(labpipe v0.3.0); run labpipe migrate"]
    assert migrations.upgrade_in_memory("config/samples.yaml", data, [DPI]) == (data, [])
    assert migrations.upgrade_in_memory("config/lab.yaml", {"plots": {}}, [DPI])[1] == []


def test_only_the_newest_release_and_unreleased_apply_in_memory():
    def one(release, old):
        return Migration(old, release, "d", ("config/lab.yaml",),
                         operations=[rename(f"plots.{old}", f"plots.{old}2")])
    ms = [one("v0.2.0", "a"), one("v0.3.0", "b"), one("Unreleased", "c")]
    data = {"plots": {"a": 1, "b": 2, "c": 3}}
    new, warnings = migrations.upgrade_in_memory("config/lab.yaml", data, ms)
    assert new == {"plots": {"a": 1, "b2": 2, "c2": 3}}
    assert len(warnings) == 2 and "unreleased labpipe" in warnings[1]


def test_upgraded_warns_once_per_file_on_stderr(project, capsys, monkeypatch):
    monkeypatch.setattr(migrations, "MIGRATIONS", [DPI])
    path = project / "config" / "lab.yaml"
    data = {"plots": {"dpi": 1}}
    assert migrations.upgraded(path, data, project) == {"plots": {"resolution": 1}}
    migrations.upgraded(path, data, project)
    assert capsys.readouterr().err.count("'plots.dpi' is now 'plots.resolution'") == 1
    assert migrations.upgraded(project / "elsewhere.yaml", data, project / "sub") == data


def _defaults(tmp_path, monkeypatch):
    config = tmp_path / "config"
    (config / "measurements" / "xrd").mkdir(parents=True, exist_ok=True)
    (config / "xrd.yaml").write_text("background: {degree: 4}\n", encoding="utf-8")
    (config / "plots.yaml").write_text((REPO / "config" / "plots.yaml").read_text(
        encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG", config)
    monkeypatch.setattr(settings, "DEFAULTS", config)
    return config


def test_settings_accept_old_keys_with_a_warning_and_still_refuse_unknown_ones(
        tmp_path, monkeypatch, capsys):
    config = _defaults(tmp_path, monkeypatch)
    ms = migrations.method_setting("0.3.0-order", "v0.3.0", "background.order is now degree",
                                   "xrd", rename("background.order", "background.degree"))
    monkeypatch.setattr(migrations, "MIGRATIONS", ms)
    (config / "lab.yaml").write_text("xrd:\n  background: {order: 6}\n", encoding="utf-8")
    (config / "samples.yaml").write_text(
        "samples:\n  S-1: {xrd: {background: {order: 7}}}\n", encoding="utf-8")
    resolved, sources = settings.resolve("xrd", "S-1", None)
    assert resolved["background"] == {"degree": 7}
    err = capsys.readouterr().err
    assert "config/lab.yaml: 'xrd.background.order' is now 'xrd.background.degree'" in err
    assert "config/samples.yaml" in err
    # the recorded hash is of the file as it is on disk
    import hashlib
    assert sources[1]["sha256"] == hashlib.sha256(
        (config / "lab.yaml").read_bytes()).hexdigest()
    (config / "measurements" / "xrd" / "S-1_a.yaml").write_text(
        "background: {ordr: 2}\n", encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="unknown setting 'background.ordr'"):
        settings.resolve("xrd", "S-1", "S-1_a")
    (config / "measurements" / "xrd" / "S-1_a.yaml").write_text(
        "background: {order: 2}\n", encoding="utf-8")       # the old name works here too
    assert settings.resolve("xrd", "S-1", "S-1_a")[0]["background"] == {"degree": 2}


def test_the_machine_ignore_and_sample_id_files_understand_old_keys(
        tmp_path, monkeypatch, capsys):
    ms = [Migration("l", "v0.3.0", "d", ("config/local.yaml",),
                    operations=[rename("automation.wait_minutes", "automation.settle_minutes")]),
          Migration("i", "v0.3.0", "d", ("config/ignore.yaml",),
                    operations=[rename("skip", "ignore")]),
          Migration("s", "v0.3.0", "d", ("config/sample_ids.yaml",),
                    operations=[rename("override", "overrides")])]
    monkeypatch.setattr(migrations, "MIGRATIONS", ms)
    config = tmp_path / "config"
    config.mkdir()
    (config / "local.yaml").write_text("raw_roots: []\nautomation: {wait_minutes: 4}\n",
                                       encoding="utf-8")
    (config / "ignore.yaml").write_text('skip: ["TEM/**"]\n', encoding="utf-8")
    (config / "sample_ids.yaml").write_text("override: {a.xy: S-1}\n", encoding="utf-8")
    assert safety.load_pipeline_config(tmp_path)["settle_minutes"] == 4
    monkeypatch.setattr(registry, "PROJECT_ROOT", tmp_path)
    assert registry.user_ignore() == ["TEM/**"]
    from labpipe import inventory, scanner
    for module in (inventory, scanner):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        assert module._overrides({"sample_id": {"overrides_file": "config/sample_ids.yaml"}}) \
            == {"a.xy": "S-1"}
    assert "config/local.yaml: 'automation.wait_minutes' is now" in capsys.readouterr().err


# ---- the command and doctor ------------------------------------------------------------

def _migrate(capsys, project, monkeypatch, *flags, answer=None):
    monkeypatch.setattr(cli, "PROJECT_ROOT", project)
    args = cli.build_parser().parse_args(["migrate", *flags])
    if answer is not None:
        monkeypatch.setattr("builtins.input", lambda prompt="": answer)
        monkeypatch.setattr("sys.stdin", type("T", (), {"isatty": lambda self: True})())
    status = cli.cmd_migrate(args, {})
    return status, json.loads(capsys.readouterr().out)


def test_migrate_shows_the_plan_and_changes_nothing(project, monkeypatch, capsys):
    monkeypatch.setattr(migrations, "MIGRATIONS", [DPI])
    status, out = _migrate(capsys, project, monkeypatch)
    assert status == 0 and out["pending"] == 1 and out["applied"] is False
    [item] = out["files"]
    assert item["file"] == "config/lab.yaml" and item["comments_kept"] is True
    assert "+  resolution: 600" in item["diff"] and "before" not in item
    assert (project / "config" / "lab.yaml").read_text(encoding="utf-8") == LAB


def test_migrate_with_nothing_to_do(project, capsys, monkeypatch):
    monkeypatch.setattr(migrations, "MIGRATIONS", [])
    status, out = _migrate(capsys, project, monkeypatch)
    assert status == 0 and out["pending"] == 0 and out["files"] == []


def test_migrate_apply_asks_then_backs_up(project, monkeypatch, capsys):
    monkeypatch.setattr(migrations, "MIGRATIONS", [DPI])
    status, out = _migrate(capsys, project, monkeypatch, "--apply", answer="n")
    assert status == 1 and out["applied"] is False
    assert (project / "config" / "lab.yaml").read_text(encoding="utf-8") == LAB
    status, out = _migrate(capsys, project, monkeypatch, "--apply", answer="y")
    assert status == 0 and out["applied"] is True and out["files"] == ["config/lab.yaml"]
    assert out["backup"].startswith("backups/migrate-") and out["comments_lost"] == []
    assert (project / out["backup"] / "config" / "lab.yaml").read_text(encoding="utf-8") == LAB
    assert "resolution: 600" in (project / "config" / "lab.yaml").read_text(encoding="utf-8")


def test_migrate_apply_yes_needs_no_terminal_but_apply_alone_does(project, monkeypatch, capsys):
    monkeypatch.setattr(migrations, "MIGRATIONS", [DPI])
    monkeypatch.setattr("sys.stdin", type("T", (), {"isatty": lambda self: False})())
    status, out = _migrate(capsys, project, monkeypatch, "--apply")
    assert status == 2 and "--yes" in out["error"]
    status, out = _migrate(capsys, project, monkeypatch, "--apply", "--yes")
    assert status == 0 and out["applied"] is True


def test_migrate_runs_on_the_host_and_in_the_parser():
    from labpipe import container
    assert "migrate" in container.LOCAL_COMMANDS
    args = cli.build_parser().parse_args(["migrate", "--apply", "--yes", "--json"])
    assert isinstance(args, argparse.Namespace) and args.apply and args.yes


def test_doctor_warns_about_pending_migrations_without_blocking(project, monkeypatch):
    monkeypatch.setattr(doctor, "PROJECT_ROOT", project)
    monkeypatch.setattr(migrations, "MIGRATIONS", [])
    assert doctor._settings_migrations() == []
    monkeypatch.setattr(migrations, "MIGRATIONS", [DPI])
    [item] = doctor._settings_migrations()
    assert item["check"] == "settings_migrations" and item["status"] == "warn"
    assert item["message"].startswith("1 settings file(s) use old names: run labpipe migrate")


# ---- the CI check ------------------------------------------------------------------------

def _script():
    spec = importlib.util.spec_from_file_location(
        "check_settings_migrations", REPO / "scripts" / "check_settings_migrations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_key_paths_of_shipped_files():
    check = _script()
    assert check.key_paths({"a": {"b": 1, "c": {"d": [1, {"e": 2}]}}, "f": None}) == {
        "a", "a.b", "a.c", "a.c.d", "f"}
    assert check._collapse({"samples.S-1.xrd", "presets.p.note", "x"}) == {
        "samples.*.xrd", "presets.*.note", "x"}


def test_a_removed_key_needs_a_migration_for_every_file_it_lives_in():
    check = _script()
    base = {"config/xrd.yaml": {"background", "background.order", "plot.dpi"},
            "config/plots.yaml": {"dpi", "font"}}
    head = {"config/xrd.yaml": {"background", "background.degree", "plot.dpi"},
            "config/plots.yaml": {"font", "resolution"}}
    kinds = {"config/xrd.yaml": "method:xrd", "config/plots.yaml": "plots"}
    problems = check.uncovered(base, head, kinds, [])
    assert [(p["file"], p["key"]) for p in problems] == [
        ("config/plots.yaml", "dpi"), ("config/xrd.yaml", "background.order")]
    assert [f for f, _ in problems[1]["needs"]] == [
        "config/lab.yaml", "config/samples.yaml", "config/samples.yaml",
        "config/measurements/xrd/x.yaml"]
    only_lab = Migration("m", "v0.3.0", "d", ("config/lab.yaml",),
                         operations=[rename("xrd.background.order", "xrd.background.degree")])
    assert len(check.uncovered(base, head, kinds, [only_lab])[1]["needs"]) == 3
    ms = migrations.method_setting("o", "v0.3.0", "d", "xrd",
                                   rename("background.order", "background.degree"))
    assert check.uncovered(base, head, kinds, [*ms, DPI]) == []        # (DPI covers plots.dpi)
    a_section = Migration("s", "v0.3.0", "d", ("config/lab.yaml",),
                          operations=[remove("plots")])
    assert check.uncovered({"config/plots.yaml": {"x", "x.y"}}, {}, kinds, [a_section]) == []


def test_changelog_must_mention_unreleased_migrations():
    check = _script()
    unreleased = Migration("0.4.0-x", "Unreleased", "d", ("config/lab.yaml",),
                           operations=[rename("plots.dpi", "plots.resolution")])
    assert check.changelog_problems([unreleased], None) == []
    log = ("# Changelog\n\n## Unreleased\n\n### Changes results\n- none, plots.dpi\n\n"
           "### Settings migrated\n- nothing\n\n### New\n- x\n\n## v0.3.0\n### Settings migrated\n"
           "- 0.4.0-x\n")
    assert len(check.changelog_problems([unreleased], log)) == 1
    log_ok = log.replace("- nothing", "- `plots.dpi` is now `plots.resolution`")
    assert check.changelog_problems([unreleased], log_ok) == []
    assert check.changelog_problems([unreleased], log.replace("- nothing", "- 0.4.0-x")) == []
    released = Migration("0.3.0-x", "v0.3.0", "d", ("config/lab.yaml",), operations=[])
    assert check.changelog_problems([released], log) == []


def _git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout


def test_the_check_on_a_small_git_repository(tmp_path, monkeypatch, capsys):
    check = _script()
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "plots.yaml").write_text("font: Arial\ndpi: 300\n", encoding="utf-8")
    (repo / "config" / "xrd.yaml").write_text("background: {order: 3}\n", encoding="utf-8")
    (repo / "config" / "samples.example.yaml").write_text(
        "presets:\n  carbon:\n    xrd: {a: 1}\nsamples: {}\n", encoding="utf-8")
    (repo / "methods.yaml").write_text(
        "methods:\n  xrd: {config: config/xrd.yaml}\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "tag", "base")
    monkeypatch.setattr(check, "ROOT", repo)
    monkeypatch.setattr(migrations, "MIGRATIONS", [])
    assert check.main("base") == 0
    assert "ok" in capsys.readouterr().out
    # a key added, an example's preset renamed: nothing needed
    (repo / "config" / "plots.yaml").write_text("font: Arial\ndpi: 300\nnew: 1\n", encoding="utf-8")
    (repo / "config" / "samples.example.yaml").write_text(
        "presets:\n  other:\n    xrd: {a: 1}\nsamples: {}\n", encoding="utf-8")
    assert check.main("base") == 0
    # a key removed or renamed without a migration
    (repo / "config" / "plots.yaml").write_text("font: Arial\nresolution: 300\n", encoding="utf-8")
    (repo / "config" / "xrd.yaml").write_text("background: {degree: 3}\n", encoding="utf-8")
    assert check.main("base") == 1
    out = capsys.readouterr().out
    assert "config/plots.yaml: the setting 'dpi'" in out and "plots.dpi" in out
    assert "config/xrd.yaml: the setting 'background.order'" in out
    # with migrations, it passes
    monkeypatch.setattr(migrations, "MIGRATIONS", [
        DPI, *migrations.method_setting("o", "v0.3.0", "d", "xrd",
                                        rename("background.order", "background.degree"))])
    assert check.main("base") == 0
    # an Unreleased one must be in the changelog
    monkeypatch.setattr(migrations, "MIGRATIONS", [
        Migration("x", "Unreleased", "d", DPI.files, operations=DPI.operations),
        *migrations.method_setting("o", "v0.3.0", "d", "xrd",
                                   rename("background.order", "background.degree"))])
    assert check.main("base") == 0                  # no CHANGELOG.md: that part is skipped
    (repo / "CHANGELOG.md").write_text("## Unreleased\n### Settings migrated\n- none\n",
                                       encoding="utf-8")
    assert check.main("base") == 1
    (repo / "CHANGELOG.md").write_text("## Unreleased\n### Settings migrated\n- x\n",
                                       encoding="utf-8")
    assert check.main("base") == 0
    assert check.main("no-such-ref") == 0


def test_the_shipped_settings_files_are_all_found():
    check = _script()
    listing = [p.relative_to(REPO).as_posix() for p in (REPO / "config").glob("*.yaml")]
    files = check.shipped_files(listing, lambda p: (REPO / p).read_text(encoding="utf-8")
                                if (REPO / p).exists() else None)
    assert {"config/plots.yaml", "config/report.yaml", "config/pipeline.yaml", "config/xrd.yaml",
            "config/sem.yaml", "config/eds.yaml", "config/lab.example.yaml"} <= set(files)
    assert files["config/xrd.yaml"] == "method:xrd"
