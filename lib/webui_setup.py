#!/usr/bin/env python3
"""hermes-spawning webui lane: vendored hermes-webui deploy + launch + serve routes.

One command per the spawn contract: spawn -> apply-stack -> webui ensure.
Idempotent; stdlib only; never kills unknown processes; only adds/updates its
own managed keys in the webui checkout's .env.

Managed per instance (all inside <home>/hermes-webui/):
  * the vendored hermes-webui checkout (from a pinned ref, kept in repo cache)
  * .env                       (mode 0600) password + gateway backend block
  * launched daemon            (in-container: ctl.sh, its own supervisor)
  * tailscale serve routes     (root -> dashboard, /webui -> chat; add-only)

Run:  python3 lib/webui_setup.py apply   --home <hermes-data> --instance <name>
                                       --control <control.env> --defaults <stack-defaults.yaml>
      python3 lib/webui_setup.py status  (same args, read-only)
"""
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
CACHE = os.path.join(REPO, ".cache", "hermes-webui")
DEFAULT_REF_FALLBACK = "2cf8e8a5eae5a42deaa888d027c408defbe76eae"
SOURCE_FALLBACK = "https://github.com/nesquena/hermes-webui.git"


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def die(msg):
    sys.stderr.write("error: " + msg + "\n")
    sys.exit(1)


def load_defaults(path):
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    try:
        import yaml  # available wherever the rest of the lane runs
        return yaml.safe_load(text) or {}
    except Exception:
        pass
    # stdlib fallback: the blocks we need are flat 2-space YAML maps
    out, grab, buf = {}, None, {}
    for line in text.splitlines():
        if re.match(r"^[a-z_]+:", line):
            if grab and out.get(grab) is None:
                out[grab] = buf
            grab = line.split(":")[0].strip()
            buf = {}
            continue
        if grab is None:
            continue
        mm = re.match(r"^  ([a-z_][a-z0-9_]*):\s*(.*)$", line)
        if mm:
            buf[mm.group(1)] = mm.group(2).strip().strip('"').strip("'")
    if grab and out.get(grab) is None:
        out[grab] = buf
    return out


def webui_cfg(defaults):
    w = ((defaults or {}).get("webui") or {})
    return {
        "enabled": str(w.get("enabled", "true")).lower() in ("1", "true", "yes", "on"),
        "source": str(w.get("source") or SOURCE_FALLBACK),
        "ref": str(w.get("ref") or DEFAULT_REF_FALLBACK),
        "route_path": str(w.get("route_path") or "/webui"),
        "listen_port": int(w.get("listen_port") or 8787),
    }


def control_value(control, key, default=""):
    try:
        for line in open(control, encoding="utf-8"):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


def clone_cache(cfg):
    src, ref = cfg["source"], cfg["ref"]
    if os.path.isdir(os.path.join(CACHE, ".git")):
        head = sh(["git", "-C", CACHE, "rev-parse", "HEAD"])
        if head.returncode == 0 and head.stdout.strip() == ref:
            return
    shutil.rmtree(CACHE, ignore_errors=True)
    os.makedirs(CACHE, exist_ok=True)
    for cmd in (["git", "-C", CACHE, "init", "-q"],
                ["git", "-C", CACHE, "remote", "add", "origin", src],
                ["git", "-C", CACHE, "fetch", "--depth", "1", "-q", "origin", ref],
                ["git", "-C", CACHE, "checkout", "-q", "FETCH_HEAD"]):
        r = sh(cmd)
        if r.returncode != 0:
            die("webui cache clone failed at: " + " ".join(cmd) + "\n" + (r.stderr or "")[:400])


def deploy(cache, home, uid, gid):
    target = os.path.join(home, "hermes-webui")
    os.makedirs(target, exist_ok=True)
    # tar copy (rsync dependency avoided); include everything except .git
    tar_from = subprocess.Popen(["tar", "-C", cache, "--exclude=.git", "-cf", "-", "."], stdout=subprocess.PIPE)
    untar = subprocess.run(["tar", "-C", target, "-xf", "-"], stdin=tar_from.stdout)
    tar_from.stdout.close()
    if tar_from.wait() != 0 or untar.returncode != 0:
        die("tar copy of webui checkout failed")
    subprocess.run(["chown", "-R", f"{uid}:{gid}", target])


def read_env_file(path):
    kv = {}
    try:
        for line in open(path, encoding="utf-8"):
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.rstrip("\n"))
            if m:
                kv[m.group(1)] = m.group(2)
    except OSError:
        pass
    return kv


def resolve_api_key(home, launch_profile):
    for p in ([os.path.join(home, ".env")] +
              ([os.path.join(home, "profiles", launch_profile, ".env")] if launch_profile and launch_profile != "default" else [])):
        kv = read_env_file(p)
        v = (kv.get("API_SERVER_KEY") or "").strip().strip('"').strip("'")
        if v:
            return v
    return ""


def probe_runs_api(base):
    """True when the instance gateway exposes the runs API on 8642.
    Route-present when auth works (200) or auth-check-fails-but-routed (401/405);
    404 / connection failure => absent.
    """
    probe = ("import urllib.request,urllib.error\n"
             "k=None\n"
             "for line in open('/opt/data/.env'):\n"
             "    if line.startswith('API_SERVER_KEY='): k=line.split('=',1)[1].strip()\n"
             "req=urllib.request.Request('http://127.0.0.1:8642/v1/runs', headers={'Authorization':'Bearer '+str(k)})\n"
             "try:\n"
             "    urllib.request.urlopen(req, timeout=8); print('RUNS 200')\n"
             "except urllib.error.HTTPError as e:\n"
             "    print('RUNS', e.code)\n"
             "except Exception as e:\n"
             "    print('RUNS ERR', repr(e)[:60])\n")
    r = sh(compose(base, "exec", "-T", "hermes", "/opt/hermes/.venv/bin/python3", "-c", probe))
    out = r.stdout or ""
    return ("RUNS 200" in out) or ("RUNS 401" in out) or ("RUNS 405" in out)


def build_env(home, launch_profile, cfg, uid, gid, runs_api="true"):
    """Assemble/upgrade <home>/hermes-webui/.env: example base + managed block,
    preserving any pre-existing values that are already set (password first)."""
    path = os.path.join(home, "hermes-webui", ".env")
    example = os.path.join(home, "hermes-webui", ".env.example")
    have = read_env_file(path)
    base_lines = []
    if os.path.exists(example):
        base_lines = open(example, encoding="utf-8").read().splitlines()
    elif os.path.exists(path):
        base_lines = open(path, encoding="utf-8").read().splitlines()
    managed = {
        "HERMES_WEBUI_LISTEN_HOST": "127.0.0.1",
        "HERMES_WEBUI_LISTEN_PORT": str(cfg["listen_port"]),
        "HERMES_WEBUI_PASSWORD": have.get("HERMES_WEBUI_PASSWORD") or secrets.token_urlsafe(24),
        "HERMES_WEBUI_CHAT_BACKEND": "gateway",
        "HERMES_WEBUI_GATEWAY_BASE_URL": "http://127.0.0.1:8642",
        "HERMES_WEBUI_GATEWAY_API_KEY": resolve_api_key(home, launch_profile),
        "HERMES_WEBUI_GATEWAY_USE_RUNS_API": "true",
    }
    if not managed["HERMES_WEBUI_GATEWAY_API_KEY"]:
        die("could not resolve API_SERVER_KEY from the instance home or launch profile .env")
    out, touched = [], set()
    if base_lines:
        for line in base_lines:
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
            if m and m.group(1) in managed:
                out.append(f"{m.group(1)}={managed[m.group(1)]}")
                touched.add(m.group(1))
            else:
                out.append(line)
    out.append("")
    out.append("# --- managed by hermes-spawning lib/webui_setup.py ---")
    for k in sorted(managed):
        if k not in touched:
            out.append(f"{k}={managed[k]}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.chmod(path, 0o600)
    try:
        os.chown(path, uid, gid)
    except PermissionError:
        pass
    return managed["HERMES_WEBUI_PASSWORD"]


def compose(base, *args):
    return ["docker", "compose", "--env-file", base["control"],
            "-f", os.path.join(REPO, "compose.yaml"), "-p", base["project"]] + list(args)


def instance_context(args):
    project = control_value(args.control, "COMPOSE_PROJECT_NAME", f"hermes-{args.instance}")
    return {"project": project, "control": os.path.abspath(args.control)}


def hermes_running(base):
    r = sh(compose(base, "ps", "--status", "running", "--services"))
    return "hermes" in (r.stdout or "")


def launch(base, restart_if_running=False):
    ok, _ = ctl_status(base)
    if ok:
        if not restart_if_running:
            return True, "already running"
        r = sh(compose(base, "exec", "-T", "-u", "10000", "-w", "/opt/data/hermes-webui",
                       "hermes", "bash", "ctl.sh", "restart"))
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    r = sh(compose(base, "exec", "-T", "-u", "10000", "-w", "/opt/data/hermes-webui",
                   "hermes", "bash", "ctl.sh", "start"))
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def ctl_status(base):
    """running-state accurate: ctl.sh prints '— running'/'— stopped'; rc is
    0 in both cases, so status must be parsed from text, not rc."""
    r = sh(compose(base, "exec", "-T", "-u", "10000", "-w", "/opt/data/hermes-webui",
                   "hermes", "bash", "ctl.sh", "status"))
    text = (r.stdout or "") + (r.stderr or "")
    running = ("— running" in text) or ("- running" in text)
    return running, text.strip()


def loopback_health(base):
    probe = ("import urllib.request,sys\n"
             "try:\n"
             "    r=urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=8)\n"
             "    print('HEALTH', r.status)\n"
             "except Exception as e:\n"
             "    print('HEALTH', repr(e)[:80])\n"
             "    sys.exit(1)\n")
    r = sh(compose(base, "exec", "-T", "hermes", "/opt/hermes/.venv/bin/python3", "-c", probe))
    ok = r.returncode == 0 and "HEALTH 200" in (r.stdout or "")
    return ok, (r.stdout or "").strip()


def ensure_routes(base, cfg):
    """Add-only route healing: root -> 9119 dashboard, /webui -> 8787 chat."""
    r = sh(compose(base, "exec", "-T", "tailscale",
                   "tailscale", "--socket=/tmp/tailscaled.sock", "serve", "status"))
    routes_root = routes_webui = False
    if r.returncode == 0:
        txt = r.stdout or ""
        # 'serve status' renders lines like: |-- /webui proxy http://127.0.0.1:8787
        for line in txt.splitlines():
            seg = line.replace("|--", " ").split()
            if len(seg) < 3:
                continue
            path_part, _proxy_word, target = seg[0], seg[1], seg[2]
            if path_part.rstrip("s") == "/" and "9119" in target:
                routes_root = True
            if path_part == cfg["route_path"] and str(cfg["listen_port"]) in target:
                routes_webui = True
    adds = []
    if not routes_root:
        adds.append(["tailscale", "--socket=/tmp/tailscaled.sock", "serve", "--bg", "--set-path", "/", "http://127.0.0.1:9119"])
    if not routes_webui:
        adds.append(["tailscale", "--socket=/tmp/tailscaled.sock", "serve", "--bg", "--set-path",
                     cfg["route_path"], f"http://127.0.0.1:{cfg['listen_port']}"])
    for cmd in adds:
        rr = sh(compose(base, "exec", "-T", "tailscale", *cmd))
        if rr.returncode != 0:
            return False, "route add failed: " + (rr.stderr or rr.stdout or "").strip()[:160]
    return (not adds), ("already present" if not adds else f"added {len(adds)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["apply", "status", "launch"])
    ap.add_argument("--home", required=True)
    ap.add_argument("--instance", required=True)
    ap.add_argument("--control", required=True)
    ap.add_argument("--defaults", required=True)
    ap.add_argument("--launch-profile", default="")
    ap.add_argument("--uid", type=int, default=int(os.environ.get("HERMES_UID", "10000")))
    ap.add_argument("--gid", type=int, default=int(os.environ.get("HERMES_GID", "10000")))
    args = ap.parse_args()

    cfg = webui_cfg(load_defaults(args.defaults))
    if not cfg["enabled"]:
        print("webui disabled in stack-defaults.yaml; nothing to do.")
        return
    base = instance_context(args)
    vendor_ok = os.path.exists(os.path.join(args.home, "hermes-webui", "ctl.sh"))

    if args.mode == "status":
        print(f"vendor: {'present' if vendor_ok else 'MISSING'}")
        if hermes_running(base):
            ok, s = ctl_status(base)
            print(f"daemon: {'running' if ok else 'down'} ({s[:100]})")
            okh, h = loopback_health(base)
            print(f"health: {'200 OK' if okh else 'FAIL'} ({h[:80]})")
        else:
            print("daemon: container not running")
        return

    password = ""
    if args.mode == "apply":
        clone_cache(cfg)
        deploy(CACHE, args.home, args.uid, args.gid)
        runs_val = "true" if (hermes_running(base) and probe_runs_api(base)) else "false"
        password = build_env(args.home, args.launch_profile, cfg, args.uid, args.gid, runs_api=runs_val)
        print(f"runs api: {runs_val}")
    if not hermes_running(base):
        print("note: hermes container not running; vendored + configured only.")
        return
    if args.mode == "apply":
        ok, msg = launch(base, restart_if_running=True)
        print(("launcher: ctl.sh start OK" if ok else "launcher: start FAILED " + msg[:160]))
        # freshly (re)started daemons need a moment to bind; poll instead of one-shot
        import time as _t
        okh, h = False, ""
        for _ in range(8):
            okh, h = loopback_health(base)
            if okh:
                break
            _t.sleep(1.5)
        print(("health: 200 OK " + h if okh else "health: FAIL " + h)[:120])
    routed, rmsg = ensure_routes(base, cfg)
    print(f"routes: {rmsg}")
    if password:
        host = control_value(args.control, "TAILSCALE_HOSTNAME", args.instance)
        print(f"webui route: {cfg['route_path']} on node '{host}' (FQDN: ./hermes-spawn.sh status {args.instance})")
        print(f"webui password: {password}")


if __name__ == "__main__":
    main()
