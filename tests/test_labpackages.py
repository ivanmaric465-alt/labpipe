"""lab/requirements.txt: the allowed form, the layer's build command (engine mocked), the
status message, provenance and replay notes, native-mode messages, `methods check`."""

import subprocess
from pathlib import Path

import pytest

from labpipe import container as ct
from labpipe import labpackages as lp
from labpipe import provenance, replay

CFG = {"name": "labpipe-pd-films", "image": "localhost/labpipe", "port": 8765}


def _project(tmp_path, text=None):
    project = tmp_path / "proj"
    (project / "lab").mkdir(parents=True)
    if text is not None:
        (project / "lab" / "requirements.txt").write_text(text, encoding="utf-8")
    return project


def test_pinned_lines_and_comments_are_accepted():
    assert lp.parse("# tools\nsix==1.17.0  # tiny\n\nscikit-image==0.25.2\n") == [
        ("six", "1.17.0"), ("scikit-image", "0.25.2")]


@pytest.mark.parametrize("line", [
    "six", "six>=1.0", "six==1.*", "six~=1.17", "-e .", "-r other.txt", "--index-url https://x",
    "https://example.org/six.whl", "six @ https://example.org/six.whl", "./local",
    "six[extra]==1.0", "six==1.0; python_version>'3'", "six==1.0 --hash=sha256:ab",
    "six===1.0", "a/b==1.0"])
def test_anything_but_name_equals_version_is_refused(line):
    with pytest.raises(lp.LabPackagesError, match="line 1"):
        lp.parse(line + "\n")


def test_a_package_listed_twice_is_refused():
    with pytest.raises(lp.LabPackagesError, match="listed twice"):
        lp.parse("Foo_Bar==1\nfoo-bar==2\n")


def test_check_without_a_file_is_fine_and_with_a_bad_one_says_why(tmp_path):
    assert lp.check(_project(tmp_path))[0] is True
    ok, detail = lp.check(_project(tmp_path / "b", "six>=1\n"))
    assert not ok and "lab/requirements.txt line 1" in detail


def test_the_layer_is_installed_with_the_images_python_under_the_bases_versions():
    text = lp.containerfile("localhost/labpipe:abc")
    assert text.startswith("FROM localhost/labpipe:abc\n")
    assert "/usr/local/bin/python -m pip install --no-cache-dir" in text
    assert "-u HTTPS_PROXY" in text      # the image's proxy only runs in the started container
    assert "pip freeze" in text and "-c /tmp/base-constraints.txt" in text and "pip check" in text
    assert "USER" not in text        # the user is what the base has


def test_image_name_follows_the_container_name():
    assert lp.lab_repository(CFG) == "localhost/labpipe-pd-films-lab"
    assert lp.lab_repository({"name": "labpipe", "image": "localhost/labpipe"}) == \
        "localhost/labpipe-lab"


def test_hash_changes_with_the_file_and_with_the_base_image(tmp_path):
    a = _project(tmp_path, "six==1.17.0\n")
    first = lp.lab_hash(a, "sha256:1")
    assert first == lp.lab_hash(a, "sha256:1") and len(first) == 12
    assert lp.lab_hash(a, "sha256:2") != first
    (a / "lab" / "requirements.txt").write_text("six==1.16.0\n", encoding="utf-8")
    assert lp.lab_hash(a, "sha256:1") != first


class _Engine:
    """A mocked engine: records every command; images are whatever `images` holds."""

    def __init__(self, images):
        self.images, self.calls = dict(images), []

    def __call__(self, command, **kw):
        self.calls.append((command, kw))
        out, code = "", 0
        if command[1:3] == ["image", "inspect"]:
            tag = command[-1]
            code = 0 if tag in self.images else 1
            out = self.images.get(tag, "")
        elif command[1] == "build":
            self.images[command[command.index("-t") + 1]] = "sha256:lab"
            self.built = (command, Path(kw["cwd"]).joinpath("Containerfile").read_text(
                encoding="utf-8"), Path(kw["cwd"]).joinpath("requirements.txt").read_text(
                encoding="utf-8"))
        return subprocess.CompletedProcess(command, code, stdout=out, stderr="")


@pytest.mark.parametrize("engine_name", ["podman", "docker"])
def test_build_lab_command(tmp_path, monkeypatch, engine_name):
    project = _project(tmp_path, "six==1.17.0\n")
    engine = _Engine({"localhost/labpipe:abc": "sha256:base"})
    monkeypatch.setattr(ct.subprocess, "run", engine)
    tag = ct.build_lab(engine_name, CFG, "localhost/labpipe:abc", project)
    command, containerfile, requirements = engine.built
    assert tag == "localhost/labpipe-pd-films-lab:" + lp.lab_hash(project, "sha256:base")
    assert command[:2] == [engine_name, "build"] and command[command.index("-t") + 1] == tag
    assert f"{ct.LAB_LABEL}={lp.sha256(project)}" in command
    assert containerfile.startswith("FROM localhost/labpipe:abc") and requirements == "six==1.17.0\n"
    joined = " ".join(command)
    assert ":z" not in joined.lower() and "label=disable" not in joined   # no mounts at build


def test_build_lab_refuses_a_bad_file_before_calling_the_engine(tmp_path, monkeypatch):
    engine = _Engine({"localhost/labpipe:abc": "sha256:base"})
    monkeypatch.setattr(ct.subprocess, "run", engine)
    with pytest.raises(ct.ContainerError, match="name==version"):
        ct.build_lab("podman", CFG, "localhost/labpipe:abc", _project(tmp_path, "six\n"))
    assert not any(c[0][1] == "build" for c in engine.calls)


def test_project_image_is_the_base_without_a_file_and_the_layer_with_one(tmp_path, monkeypatch):
    engine = _Engine({"localhost/labpipe:abc": "sha256:base"})
    monkeypatch.setattr(ct.subprocess, "run", engine)
    assert ct.project_image("podman", CFG, "localhost/labpipe:abc", _project(tmp_path)) \
        == "localhost/labpipe:abc"
    assert not any(c[0][1] == "build" for c in engine.calls)
    project = _project(tmp_path / "b", "six==1.17.0\n")
    tag = ct.project_image("podman", CFG, "localhost/labpipe:abc", project)
    assert tag.startswith("localhost/labpipe-pd-films-lab:") and engine.built
    engine.built = None
    assert ct.project_image("podman", CFG, "localhost/labpipe:abc", project) == tag   # kept
    assert engine.built is None
    (project / "lab" / "requirements.txt").write_text("six==1.16.0\n", encoding="utf-8")
    assert ct.project_image("podman", CFG, "localhost/labpipe:abc", project) != tag  # rebuilt
    assert engine.built is not None


def test_status_says_when_the_layer_is_out_of_date(tmp_path, monkeypatch):
    project = _project(tmp_path, "six==1.17.0\n")
    monkeypatch.setattr(ct, "_root", lambda: project)
    monkeypatch.setattr(ct, "_prepare", lambda: ({}, CFG, "podman"))
    monkeypatch.setattr(ct, "container_hash", lambda code=None: "abc")
    monkeypatch.setattr(ct, "claude_login_present", lambda engine: False)

    def state(labels):
        monkeypatch.setattr(ct, "inspect", lambda engine, name: {
            "State": {"Running": True}, "Config": {"Labels": labels}})
        return ct.status_record()

    current = {ct.HASH_LABEL: "abc", ct.LAB_LABEL: lp.sha256(project)}
    assert state(current)["lab_packages_up_to_date"] is True and state(current)["next"] is None
    old = state({**current, ct.LAB_LABEL: "0" * 64})
    assert old["lab_packages"] == ("lab/requirements.txt changed: labpipe container build, "
                                   "then labpipe container start --replace")
    assert old["next"].startswith("labpipe container build")
    (project / "lab" / "requirements.txt").unlink()           # file removed: layer is stale too
    assert state(current)["lab_packages_up_to_date"] is False


def test_status_of_a_project_without_a_file_is_unchanged(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(ct, "_root", lambda: project)
    monkeypatch.setattr(ct, "_prepare", lambda: ({}, CFG, "podman"))
    monkeypatch.setattr(ct, "container_hash", lambda code=None: "abc")
    monkeypatch.setattr(ct, "claude_login_present", lambda engine: False)
    monkeypatch.setattr(ct, "inspect", lambda engine, name: {
        "State": {"Running": True}, "Config": {"Labels": {ct.HASH_LABEL: "abc"}}})
    record = ct.status_record()
    assert record["lab_packages"] is None and record["next"] is None


# ---- provenance, replay, native messages ---------------------------------------------------

def test_environment_records_the_file_and_the_installed_versions(tmp_path, monkeypatch):
    project = _project(tmp_path, "pytest==0.0.0\nno-such-package-xyz==1.0\n")
    monkeypatch.setattr(provenance, "PROJECT_ROOT", project)
    env = provenance.environment()
    assert env["lab_requirements_sha256"] == lp.sha256(project)
    assert env["lab_packages"]["no-such-package-xyz"] is None
    assert env["lab_packages"]["pytest"]            # installed (any version)


def test_environment_without_a_file_records_none(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "PROJECT_ROOT", _project(tmp_path))
    env = provenance.environment()
    assert env["lab_requirements_sha256"] is None and env["lab_packages"] == {}


def test_replay_warns_when_the_lab_packages_differ(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "PROJECT_ROOT", _project(tmp_path, "pytest==0.0.0\n"))
    record = {"environment": {**provenance.environment(), "lab_requirements_sha256": "0" * 64,
                              "lab_packages": {"pytest": "0.0.1", "gone-pkg": "2.0"}}}
    text = "\n".join(replay.environment_notes(record))
    assert "lab/requirements.txt differs" in text
    assert "recorded lab package pytest 0.0.1, now " in text
    assert "recorded lab package gone-pkg 2.0, now not installed" in text


def test_replay_ignores_lab_packages_in_records_that_never_had_them(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "PROJECT_ROOT", _project(tmp_path, "six==1.17.0\n"))
    old = {k: v for k, v in provenance.environment().items()
           if not k.startswith("lab_")}
    assert not [n for n in replay.environment_notes({"environment": old}) if "lab" in n]


def test_native_mode_prints_the_install_command_and_never_runs_it(tmp_path, monkeypatch):
    project = _project(tmp_path, "no-such-package-xyz==1.0\n")
    monkeypatch.setattr("labpipe.safety.in_container", lambda: False)
    status, message, step = lp.problem(project, host_container=False, in_container=False)
    assert status == "todo" and "no-such-package-xyz==1.0 (not installed)" in message
    import sys
    assert step == f'the user runs: "{sys.executable}" -m pip install -r "{project}/lab/requirements.txt"'
    ok, detail = lp.check_result(project, True)
    assert not ok and "pip install -r" in detail
    assert lp.check_result(project, False)[0] is True       # a shipped method does not need them
    assert lp.problem(project, host_container=True, in_container=False) is None  # status covers it


def test_inside_the_container_the_message_is_to_build_the_layer(tmp_path):
    project = _project(tmp_path, "no-such-package-xyz==1.0\n")
    _, _, step = lp.problem(project, host_container=False, in_container=True)
    assert "labpipe container build" in step and "pip install" not in step


def test_a_bad_file_is_a_doctor_todo_even_in_container_mode(tmp_path):
    status, message, _ = lp.problem(_project(tmp_path, "six>=1\n"), host_container=True,
                                    in_container=False)
    assert status == "todo" and "name==version" in message
