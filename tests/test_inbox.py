"""The notifications record and the read-only browser page."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from labpipe import automation, inbox, safety

TOKEN = "s3cret-token"
CONFIG = {"logs_dir": "logs", "analysis_dir": "analysis", "manifest": "manifest.json",
          "protected_paths": [], "automation": {"notify": {"desktop": False}}}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(automation, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(safety, "PROJECT_ROOT", tmp_path)
    (tmp_path / "manifest.json").write_text("{\"secret\": 1}")
    return tmp_path


@pytest.fixture
def server(project):
    automation.notify(CONFIG, "labpipe: 1 new file(s) not recognised: a.xrdml", kind="unrecognised")
    automation.notify(CONFIG, "labpipe: analysis started", kind="started")
    automation.notify(CONFIG, "labpipe: analysis done <b>x</b>", kind="finished")
    httpd = inbox.make_server(CONFIG, host="127.0.0.1", port=0, token=TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(url, headers=None, method="GET"):
    request = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as err:
        return err.code, err.read(), err.headers


def test_notify_records_kind_and_needs_you(project):
    automation.notify(CONFIG, "one", kind="failed")
    automation.notify(CONFIG, "two", kind="finished")
    automation.notify(CONFIG, "three")
    rows = [json.loads(l) for l in (project / "logs" / "notifications.jsonl").read_text().splitlines()]
    assert [r["id"] for r in rows] == [1, 2, 3]
    assert [r["needs_you"] for r in rows] == [True, False, False]
    assert [r["kind"] for r in rows] == ["failed", "finished", "info"]
    assert rows[0]["message"] == "one" and rows[0]["utc"]
    assert "notify" in (project / "logs" / "automation.log").read_text()


def test_ids_continue_after_restart(project):
    automation.notify(CONFIG, "a", kind="test")
    path = project / "logs" / "notifications.jsonl"
    assert automation._last_notification_id(path) == 1
    automation.notify(CONFIG, "b", kind="test")
    assert [json.loads(l)["id"] for l in path.read_text().splitlines()] == [1, 2]
    assert automation._last_notification_id(project / "logs" / "none.jsonl") == 0


def test_token_required_and_cookie(server):
    code, body, _ = get(server + "/")
    assert code == 403 and b"labpipe container status" in body
    assert get(server + "/?token=wrong")[0] == 403
    code, body, headers = get(server + f"/?token={TOKEN}")
    assert code == 200 and b"<title>labpipe</title>" in body
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    cookie = headers["Set-Cookie"].split(";")[0]
    assert get(server + "/api/notifications", {"Cookie": cookie})[0] == 200
    assert get(server + "/api/notifications", {"Cookie": "labpipe_token=nope"})[0] == 403
    assert get(server + "/report")[0] == 403


def test_api_after(server):
    cookie = {"Cookie": f"labpipe_token={TOKEN}"}
    data = json.loads(get(server + "/api/notifications", cookie)[1])
    assert [i["id"] for i in data["items"]] == [1, 2, 3] and data["last_id"] == 3
    assert data["items"][0]["needs_you"] is True
    data = json.loads(get(server + f"/api/notifications?after=2&token={TOKEN}")[1])
    assert [i["id"] for i in data["items"]] == [3]
    data = json.loads(get(server + "/api/notifications?after=3", cookie)[1])
    assert data == {"items": [], "last_id": 3}


def test_report_and_review(server, project):
    auth = f"?token={TOKEN}"
    code, body, _ = get(server + "/report" + auth)
    assert code == 404 and b"not built" in body
    (project / "analysis").mkdir()
    (project / "analysis" / "report.html").write_text("<html>REPORT</html>")
    code, body, headers = get(server + "/report" + auth)
    assert code == 200 and body == b"<html>REPORT</html>" and "text/html" in headers["Content-Type"]
    assert get(server + "/review" + auth)[0] == 404
    (project / "logs" / "review_queue.md").write_text("# Review\nčšž\n", encoding="utf-8")
    code, body, headers = get(server + "/review" + auth)
    assert code == 200 and "čšž" in body.decode("utf-8")
    assert headers["Content-Type"] == "text/plain; charset=utf-8"


def test_nothing_else_is_served(server):
    auth = f"?token={TOKEN}"
    for path in ("/manifest.json", "/logs/notifications.jsonl", "/report/../manifest.json",
                 "/%2e%2e/", "/%2e%2e/manifest.json", "/report/", "/api", "/..%2fmanifest.json",
                 "//etc/passwd"):
        code, body, _ = get(server + path + auth)
        assert code == 404, path
        assert b"secret" not in body
    for method in ("POST", "PUT", "DELETE"):
        assert get(server + "/" + auth, method=method)[0] == 405
