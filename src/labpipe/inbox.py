"""The notifications page: a read-only local web page (standard library only).

Served by the watcher inside the container (`automation.watch`) on LABPIPE_PORT; the host
publishes the port on 127.0.0.1 only. Needs the token from `labpipe container status`.
Serves exactly four things: the page, /api/notifications, /report and /review.
"""

from __future__ import annotations

import hmac
import json
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

from labpipe import automation

COOKIE = "labpipe_token"
MAX_ITEMS = 200
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; "
       "script-src 'unsafe-inline'")


def url(port: int | str, token: str) -> str:
    return f"http://localhost:{port}/?token={quote(token, safe='')}"


def read_notifications(config: dict, after: int = 0) -> dict:
    """Items with id > after (oldest first, at most MAX_ITEMS newest) and the last id."""
    items, last = [], 0
    try:
        text = automation.notifications_path(config).read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        try:
            item = json.loads(line)
            number = int(item["id"])
        except (ValueError, KeyError, TypeError):
            continue
        last = max(last, number)
        if number > after:
            items.append(item)
    return {"items": items[-MAX_ITEMS:], "last_id": last}


def make_server(config: dict, host: str = "0.0.0.0", port: int = 8765,
                token: str = "") -> ThreadingHTTPServer:
    if not token:
        raise ValueError("a page token is required")

    class Handler(BaseHTTPRequestHandler):
        server_version = "labpipe"

        def log_message(self, *args):   # keep the watcher's output clean
            pass

        def _send(self, code: int, body: bytes, kind: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CSP)
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _text(self, code: int, message: str, extra: dict | None = None):
            self._send(code, message.encode("utf-8"), "text/plain; charset=utf-8", extra)

        def _authorised(self, query: dict) -> tuple[bool, bool]:
            """(allowed, set_cookie)"""
            given = (query.get("token") or [""])[0]
            if given and hmac.compare_digest(given.encode(), token.encode()):
                return True, True
            jar = cookies.SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except cookies.CookieError:
                return False, False
            morsel = jar.get(COOKIE)
            ok = bool(morsel and hmac.compare_digest(morsel.value.encode(), token.encode()))
            return ok, False

        def do_GET(self):
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            allowed, fresh = self._authorised(query)
            if not allowed:
                return self._text(403, "Forbidden: open the link from `labpipe container "
                                       "status`.\n")
            extra = ({"Set-Cookie": f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict"}
                     if fresh else None)
            root = automation.PROJECT_ROOT
            if parts.path == "/":
                return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8", extra)
            if parts.path == "/api/notifications":
                try:
                    after = int((query.get("after") or ["0"])[0])
                except ValueError:
                    after = 0
                body = json.dumps(read_notifications(config, after), ensure_ascii=False)
                return self._send(200, body.encode("utf-8"), "application/json", extra)
            if parts.path == "/report":
                path = root / config["analysis_dir"] / "report.html"
                kind, missing = "text/html; charset=utf-8", \
                    "The report is not built yet (it appears after the first analysis).\n"
            elif parts.path == "/review":
                path = root / config["logs_dir"] / "review_queue.md"
                kind, missing = "text/plain; charset=utf-8", "No review queue yet.\n"
            else:
                return self._text(404, "Not found.\n")
            try:
                body = path.read_bytes()
            except OSError:
                return self._text(404, missing)
            self._send(200, body, kind, extra)

        def _refuse(self):
            self._text(405, "Read-only page: GET only.\n", {"Allow": "GET"})

        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _refuse

    return ThreadingHTTPServer((host, port), Handler)


def serve(config: dict, host: str = "0.0.0.0", port: int = 8765, token: str = "") -> None:
    """Serve until the process ends (blocking)."""
    make_server(config, host, port, token).serve_forever()


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>labpipe</title>
<style>
:root{--bg:#fbfbf8;--fg:#1c1c1a;--mut:#66665f;--line:#deded6;--card:#ffffff;--warn:#9a5b00;
--bad:#a3262a;--ok:#2f6b3a;--chip:#f1f0ea;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161614;--fg:#ebebe6;
--mut:#a3a39b;--line:#383833;--card:#1f1f1c;--warn:#e0a54a;--bad:#ef8a8c;--ok:#8fcf99;
--chip:#2a2a26;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#161614;--fg:#ebebe6;--mut:#a3a39b;--line:#383833;--card:#1f1f1c;
--warn:#e0a54a;--bad:#ef8a8c;--ok:#8fcf99;--chip:#2a2a26;color-scheme:dark}
body{background:var(--bg);color:var(--fg);margin:0;padding-inline:16px;
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:800px;margin:0 auto;padding-block:28px 60px}
h1{font-size:1.6rem;margin:0}h2{font-size:1.05rem;margin:1.6rem 0 .5rem}
.sub,.meta{color:var(--mut);font-size:.85rem}a{color:inherit}
ul{list-style:none;margin:0;padding:0;border:1px solid var(--line);border-radius:8px;
background:var(--card)}
li{padding:.6rem .8rem;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
li:last-child{border-bottom:0}.need{border-left:3px solid var(--warn)}
.empty{color:var(--mut);padding:.6rem .8rem}.err{color:var(--bad);margin:.6rem 0}
button{font:inherit;border:1px solid var(--line);background:var(--chip);color:var(--fg);
border-radius:6px;padding:.3rem .7rem;cursor:pointer}
.dismiss{margin-left:.6rem;font-size:.8rem;color:var(--mut);cursor:pointer}
</style></head><body><main>
<h1>labpipe</h1>
<p class="sub"><a href="/report">Report</a> · <a href="/review">Review queue</a></p>
<p><button id="perm" hidden>Show desktop notifications</button></p>
<div id="err" class="err" hidden>container not reachable</div>
<h2>Needs you</h2><ul id="need"></ul>
<h2>Activity</h2><ul id="all"></ul>
<p class="sub" id="checked"></p>
</main>
<script>
(function(){
var items=[],dismissed=[],seen=null;
function load(key,fallback){try{var v=localStorage.getItem(key);return v===null?fallback:JSON.parse(v)}catch(e){return fallback}}
function save(key,v){try{localStorage.setItem(key,JSON.stringify(v))}catch(e){}}
dismissed=load("labpipe_dismissed",[]);if(!Array.isArray(dismissed))dismissed=[];
seen=load("labpipe_seen",null);
var $=function(id){return document.getElementById(id)};
function when(utc){var d=new Date(utc);return isNaN(d)?utc:d.toLocaleString()}
function row(it,withDismiss){
  var li=document.createElement("li");if(withDismiss)li.className="need";
  var m=document.createElement("div");m.textContent=it.message;li.appendChild(m);
  var t=document.createElement("span");t.className="meta";t.textContent=when(it.utc);li.appendChild(t);
  if(withDismiss){var a=document.createElement("a");a.className="dismiss";a.textContent="dismiss";
    a.href="#";a.onclick=function(e){e.preventDefault();dismissed.push(it.id);save("labpipe_dismissed",dismissed);render()};
    li.appendChild(a)}
  return li}
function fill(ul,list,withDismiss,empty){
  ul.textContent="";
  if(!list.length){var li=document.createElement("li");li.className="empty";li.textContent=empty;ul.appendChild(li);return}
  list.forEach(function(it){ul.appendChild(row(it,withDismiss))})}
function render(){
  var newest=items.slice().reverse();
  fill($("need"),newest.filter(function(i){return i.needs_you&&dismissed.indexOf(i.id)<0}),true,"Nothing needs you.");
  fill($("all"),newest,false,"No activity yet.")}
function pad(n){return (n<10?"0":"")+n}
function canNotify(){return "Notification" in window&&Notification.permission==="granted"}
if("Notification" in window&&Notification.permission==="default"){
  $("perm").hidden=false;
  $("perm").onclick=function(){Notification.requestPermission().then(function(){$("perm").hidden=Notification.permission!=="default"})}}
function poll(){
  fetch("/api/notifications?after=0",{credentials:"same-origin",cache:"no-store"}).then(function(r){
    if(!r.ok)throw new Error(r.status);return r.json()}).then(function(data){
    $("err").hidden=true;items=data.items;
    if(seen===null)seen=data.last_id;   // first visit: do not announce the whole history
    else if(canNotify())items.forEach(function(it){
      if(it.id>seen){try{new Notification("labpipe",{body:it.message})}catch(e){}}});
    if(data.last_id>seen||seen===null){seen=data.last_id}
    save("labpipe_seen",seen);
    var d=new Date();$("checked").textContent="last checked "+pad(d.getHours())+":"+pad(d.getMinutes());
    render()}).catch(function(){$("err").hidden=false})}
render();poll();setInterval(poll,30000);
})();
</script></body></html>
"""
