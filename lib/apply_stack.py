#!/usr/bin/env python3
"""Apply / audit the fleet model-stack defaults on a Hermes instance home.

Reads stack-defaults.yaml from the repository and makes every persona (+ the
default profile) of a hermes-spawning instance converge on it:

  * one endpoint (host CLIProxy) custom providers: ccs-astra, ccs-anthropic,
    ccs-kimi, ccs-deepseek, ccs-glm
  * primary gpt-6-astra (high) + 4-model fallback chain (all max)
  * claude-haiku-4-5 on all 8 auxiliary tasks, every persona
  * approvals.mode = off everywhere
  * reactions off everywhere (YAML + .env layer; env wins in Hermes)
  * expanded thinking for every persona
  * cron model left null (jobs inherit the persona config; per-job pins stay
    and are only reported)
  * multiplexed gateway + fleet-sized backends when >1 persona

apply mode is idempotent, merges (never wipes persona-specific keys), backs up
each file it rewrites, and sets ownership to the instance UID/GID when given.

status mode is read-only and exits 1 on any drift ("verified on disk").

Usage:
  apply_stack.py apply  --home DIR [--launch-profile NAME] --base-url URL --api-key KEY
                        [--defaults FILE] [--uid UID] [--gid GID] [--quiet]
  apply_stack.py status --home DIR [--launch-profile NAME] --base-url URL --api-key KEY
                        [--defaults FILE] [--json] [--quiet]
"""
from __future__ import annotations

import argparse, copy, glob, json, os, re, shutil, sys, time

try:
    import yaml
except ImportError:
    sys.stderr.write("error: PyYAML is required (python3-yaml).\n")
    sys.exit(2)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS_CANDIDATES = os.path.join(REPO_ROOT, "stack-defaults.yaml")

# --------------------------------------------------------------------- yaml io
def _load_yaml(path):
    with open(path) as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}

def _dump_yaml(data):
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, width=140, allow_unicode=True)

def _read_file(path):
    with open(path) as fh:
        return fh.read()

# ------------------------------------------------------------------ utilities
def utc_stamp():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

def backup(path, keep=5):
    bak = f"{path}.bak-stack-{utc_stamp()}"
    shutil.copy2(path, bak)
    baks = sorted(glob.glob(f"{path}.bak-stack-*"))
    for old in baks[:-keep]:
        try: os.unlink(old)
        except OSError: pass

def write_text(path, content, mode=None, uid=None, gid=None, backups=True):
    existed = os.path.exists(path)
    if existed:
        st = os.stat(path)
        mode = mode if mode is not None else (st.st_mode & 0o777)
        if backups:
            backup(path)
    mode = mode if mode is not None else 0o600
    tmp = f"{path}.tmp-stack-{os.getpid()}"
    with open(tmp, "w") as fh:
        fh.write(content)
    os.chmod(tmp, mode)
    if uid is not None and gid is not None:
        try: os.chown(tmp, uid, gid)
        except OSError: pass
    os.replace(tmp, path)

class Reporter:
    def __init__(self, quiet=False):
        self.quiet = quiet
        self.issues = []      # misconfigurations (drift)
        self.notes = []       # informational
        self.wrote = []       # files written in apply mode
    def issue(self, where, what):
        self.issues.append((where, what))
    def note(self, where, what):
        self.notes.append((where, what))
    def wrote_it(self, path):
        self.wrote.append(path)
    def out(self, line):
        if not self.quiet:
            print(line)
    def dump(self, json_mode=False):
        if json_mode:
            print(json.dumps({
                "drift": [f"{w}: {wht}" for w, wht in self.issues],
                "notes": [f"{w}: {wht}" for w, wht in self.notes],
                "written": self.wrote,
            }, indent=1))
            return
        for w in self.wrote:
            self.out(f"  wrote    {w}")
        for w, what in self.notes:
            self.out(f"  note     {w}: {what}")
        for w, what in self.issues:
            self.out(f"  DRIFT    {w}: {what}")

# ------------------------------------------------------------------ deep merge
def deep_merge(dst, src):
    """Merge src into dst in place (dicts merge, everything else replaces)."""
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst

def get(cfg, key, default=None):
    return cfg.get(key, default) if isinstance(cfg, dict) else default

def ensure_dict(cfg, key):
    if not isinstance(cfg.get(key), dict):
        cfg[key] = {}
    return cfg[key]

# ------------------------------------------------------------------ personas
def personas(home):
    """['default'] + sorted persona dir names."""
    ps = []
    base = os.path.join(home, "profiles")
    for d in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if os.path.isdir(os.path.join(base, d)):
            ps.append(d)
    return ["default"] + ps

def config_path(home, persona):
    return os.path.join(home, "config.yaml") if persona == "default" \
        else os.path.join(home, "profiles", persona, "config.yaml")

def env_path(home, persona):
    return os.path.join(home, ".env") if persona == "default" \
        else os.path.join(home, "profiles", persona, ".env")

def jobs_path(home, persona):
    return os.path.join(home, "cron", "jobs.json") if persona == "default" \
        else os.path.join(home, "profiles", persona, "cron", "jobs.json")

# -------------------------------------------------------------- expectations
def build_persona_patch(defaults, base_url, api_key):
    """Config fragment merged into EVERY persona (+default) config."""
    d = defaults
    providers = []
    for name, spec in (d.get("providers") or {}).items():
        entry = {
            "name": name,
            "base_url": base_url,
            "api_key": api_key,
            "model": spec.get("model"),
            "models": {m: {} for m in (spec.get("models") or [])},
        }
        if spec.get("api_mode"):
            entry["api_mode"] = spec["api_mode"]
        if spec.get("extra_body"):
            entry["extra_body"] = spec["extra_body"]
        providers.append((name, entry))

    aux_spec = d.get("auxiliary") or {}
    aux_chain = []
    for entry in (aux_spec.get("fallback_chain") or []):
        if isinstance(entry, dict) and entry.get("provider") and entry.get("model"):
            aux_chain.append({
                "provider": f"custom:{entry['provider']}",
                "model": entry["model"],
            })
    aux = {}
    for task in (aux_spec.get("tasks") or []):
        block = {
            "provider": f"custom:{aux_spec['provider']}",
            "model": aux_spec["model"],
        }
        # Explicit empty effort = omit on the wire (no inherited extra effort
        # on background lanes; see stack-defaults.yaml auxiliary note).
        if "reasoning_effort" in aux_spec:
            block["reasoning_effort"] = aux_spec["reasoning_effort"]
        if aux_chain:
            block["fallback_chain"] = list(aux_chain)
        if task in (aux_spec.get("prefer_fast_model_tasks") or []):
            block["prefer_fast_model"] = True
        aux[task] = block

    patch = {
        "model": {
            "default": d["models"]["primary"]["name"],
            "provider": f"custom:{d['models']['primary']['provider']}",
            "base_url": base_url,
        },
        "agent": {
            "reasoning_effort": (d.get("agent") or {}).get("reasoning_effort", "high"),
            "reasoning_overrides": dict((d.get("agent") or {}).get("reasoning_overrides") or {}),
        },
        "compression": {"threshold": (d.get("compression") or {}).get("threshold", 0.25)},
        "auxiliary": aux,
        "approvals": {"mode": (d.get("approvals") or {}).get("mode", "off")},
        "display": {
            "show_reasoning": True,
            "thinking_mode": (d.get("thinking") or {}).get("thinking_mode", "full"),
            "details_mode": (d.get("thinking") or {}).get("details_mode", "expanded"),
            "message_reactions": False,
        },
        "cron": {
            "model": (d.get("cron") or {}).get("model", ""),
            "model_provider": (d.get("cron") or {}).get("model_provider", ""),
        },
        "__providers__": providers,
    }
    for section, spec in (d.get("reactions_off", {}).get("config") or {}).items():
        patch[section] = spec
    thinking = d.get("thinking") or {}
    patch["display"]["show_reasoning"] = bool(thinking.get("show_reasoning", True))
    return patch

def canonical_fallbacks(defaults):
    return [{
        "provider": f"custom:{fb['provider']}",
        "model": fb["name"],
        "reasoning_effort": fb.get("reasoning_effort", "max"),
    } for fb in (defaults.get("models", {}).get("fallbacks") or [])]

def canonical_provider_names(defaults):
    return set((defaults.get("providers") or {}).keys())

def backends_target(defaults, person_count):
    b = defaults.get("backends") or {}
    runs = person_count * int(b.get("per_persona", 2))
    runs = max(int(b.get("min_concurrent_runs", 10)), runs)
    runs = min(int(b.get("cap_concurrent_runs", 32)), runs)
    live = min(person_count + int(b.get("live_sessions_margin", 8)),
               int(b.get("live_sessions_cap", 128)))
    return runs, live

def merge_ccs_providers(cfg_providers, ccs_entries):
    """Merge CCS provider entries by name into an existing provider list."""
    out = [e for e in (cfg_providers or []) if isinstance(e, dict)]
    idx = {e.get("name"): i for i, e in enumerate(out)}
    for _, entry in ccs_entries:
        if entry["name"] in idx:
            out[idx[entry["name"]]] = entry
        else:
            out.append(entry)
    return out

def merged_fallbacks(cfg_fallbacks, canonical, ccs_names):
    """Canonical chain first, then any pre-existing non-CCS entries, deduped."""
    kept = []
    seen = set()
    for fb in (cfg_fallbacks or []):
        prov, model = "", ""
        if isinstance(fb, dict):
            prov = str(fb.get("provider") or "")
            model = str(fb.get("model") or "")
        key = (prov, model)
        if prov.startswith("custom:") and prov[7:] in ccs_names:
            continue  # replaced by canonical chain
        if key in seen:
            continue
        seen.add(key)
        kept.append(fb)
    return list(canonical) + kept

# --------------------------------------------------------------- env upserts
def env_upserts(defaults):
    return {
        "TELEGRAM_REACTIONS": "false", "DISCORD_REACTIONS": "false",
        "SLACK_REACTIONS": "false", "MATRIX_REACTIONS": "false",
        "SIGNAL_REACTIONS": "false", "FEISHU_REACTIONS": "false",
    }

def upsert_env_file(path, items, uid=None, gid=None):
    lines = []
    if os.path.exists(path):
        with open(path) as fh:
            lines = fh.read().splitlines()
    touched = set()
    out = []
    for line in lines:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if m and m.group(1) in items:
            val = items[m.group(1)]
            out.append(f"{m.group(1)}={val}")
            touched.add(m.group(1))
        else:
            out.append(line)
    for k in sorted(items):
        if k not in touched:
            out.append(f"{k}={items[k]}")
    write_text(path, "\n".join(out) + "\n", mode=0o600, uid=uid, gid=gid)

def check_env_file(path, items, where, rep):
    existing = {}
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
                if m:
                    existing[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    for k, want in sorted(items.items()):
        have = existing.get(k)
        if have != want:
            rep.issue(where, f".env {k}={have!r} != {want!r}")

# ----------------------------------------------------------------- reconcile
def gateway_patch(defaults, person_count):
    runs, live = backends_target(defaults, person_count)
    b = defaults.get("backends") or {}
    return {
        "gateway": {
            "multiplex_profiles": bool(b.get("multiplex_profiles", True)),
            "auto_multiplex_migration": bool(b.get("auto_multiplex_migration", True)),
            "api_server": {"max_concurrent_runs": runs},
        },
        "max_live_sessions": live,
    }, runs, live

# ------------------------------------------------------------------ compare
def flattened(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        p = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.update(flattened(v, p))
        else:
            out[p] = v
    return out

def compare_persona(home, persona, actual, patch, canonical_fb, ccs_names, rep):
    where = persona
    for section, spec in patch.items():
        if section.startswith("__"):
            continue
        if not isinstance(spec, dict):
            continue
        want = flattened({section: spec})
        have = flattened({section: actual.get(section)})
        for k, want_v in want.items():
            have_v = have.get(k)
            if have_v != want_v:
                rep.issue(where, f"{k} = {have_v!r} (want {want_v!r})")
    # providers
    want_by_name = {n: e for n, e in patch["__providers__"]}
    have_by_name = {e.get("name"): e for e in (actual.get("custom_providers") or []) if isinstance(e, dict)}
    for name, want in want_by_name.items():
        entry = have_by_name.get(name)
        if entry is None:
            rep.issue(where, f"custom_providers: missing provider {name}")
            continue
        for k in ("base_url", "api_key", "model", "api_mode"):
            if want.get(k) is not None and entry.get(k) != want.get(k):
                shown = ("***" if k == "api_key" and want.get(k) else want.get(k))
                rep.issue(where, f"custom_providers[{name}].{k} = {entry.get(k)!r} (want {shown!r})")
    # stray model-section overrides must not fight the provider entry
    for stray in ("api_key", "api_mode"):
        if isinstance(actual.get("model"), dict) and stray in actual["model"]:
            rep.issue(where, f"model.{stray} is set (stray override; apply removes it)")
    # provider model maps expose the expected aliases
    for name, want in want_by_name.items():
        entry = have_by_name.get(name) or {}
        models = entry.get("models") or {}
        missing_models = [m for m in (want.get("models") or {}) if m not in models]
        for m in missing_models:
            rep.issue(where, f"custom_providers[{name}].models is missing {m}")
    # fallback chain: canonical entries must be present first, in order
    have_fb = actual.get("fallback_model") or []
    keys = [f"{fb.get('provider')}:{fb.get('model')}" for fb in have_fb if isinstance(fb, dict)]
    canon = [f"{fb['provider']}:{fb['model']}" for fb in canonical_fb]
    if keys[:len(canon)] != canon:
        rep.issue(where, f"fallback_model chain is {keys[:len(canon)] or 'empty'} (want prefix {canon})")

def process(home, defaults, base_url, api_key, launch_profile, mode, rep, uid=None, gid=None):
    ccs_names = canonical_provider_names(defaults)
    canon_fb = canonical_fallbacks(defaults)
    patch = build_persona_patch(defaults, base_url, api_key)
    patch["__canonical_fb__"] = canon_fb
    ps = personas(home)
    n = len(ps)
    runs, live = backends_target(defaults, n)
    multiplex_target = n > 1

    for persona in ps:
        cpath = config_path(home, persona)
        existed = os.path.exists(cpath)
        actual = _load_yaml(cpath) if existed else {}
        if mode == "apply":
            new = copy.deepcopy(actual)
            for section, spec in patch.items():
                if section.startswith("__"):
                    continue
                ensure_dict(new, section)
                deep_merge(new[section], spec)
            # Strip model-section overrides that would beat the provider entry
            # (a leftover model.api_key from a retired provider breaks auth).
            for stray in ("api_key", "api_mode"):
                if isinstance(new.get("model"), dict) and stray in new["model"]:
                    del new["model"][stray]
            new["custom_providers"] = merge_ccs_providers(actual.get("custom_providers"), patch["__providers__"])
            new["fallback_model"] = merged_fallbacks(actual.get("fallback_model"), canon_fb, ccs_names)
            if persona == launch_profile and n > 1:
                gp, _runs_n, live_n = gateway_patch(defaults, n)
                deep_merge(new, gp)
                new["max_live_sessions"] = live_n
            content = _dump_yaml(new)
            prev = _read_file(cpath) if existed else None
            if prev != content:
                write_text(cpath, content, mode=0o640, uid=uid, gid=gid)
                rep.wrote_it(cpath)
        else:
            compare_persona(home, persona, actual, patch, canon_fb, ccs_names, rep)
            if persona == launch_profile and n > 1:
                gw = actual.get("gateway") or {}
                want_mp = True
                if gw.get("multiplex_profiles") is not True:
                    rep.issue(persona, f"gateway.multiplex_profiles={gw.get('multiplex_profiles')!r} (want true: {n} personas)")
                if gw.get("auto_multiplex_migration") is not True:
                    rep.issue(persona, f"gateway.auto_multiplex_migration={gw.get('auto_multiplex_migration')!r} (want true)")
                want_runs, want_live = backends_target(defaults, n)
                have_runs = ((gw.get("api_server") or {}).get("max_concurrent_runs"))
                if have_runs != want_runs:
                    rep.issue(persona, f"gateway.api_server.max_concurrent_runs={have_runs!r} (want {want_runs} for {n} personas)")
                have_live = actual.get("max_live_sessions")
                if have_live != want_live:
                    rep.issue(persona, f"max_live_sessions={have_live!r} (want {want_live} for {n} personas)")
        # .env reaction blockers
        epath = env_path(home, persona)
        if mode == "apply":
            upsert_env_file(epath, env_upserts(defaults), uid=uid, gid=gid)
        else:
            check_env_file(epath, env_upserts(defaults), persona, rep)

    # scheduled-jobs policy audit (informational): jobs must default to null
    for persona in ps:
        jp = jobs_path(home, persona)
        if os.path.exists(jp):
            try:
                jobs = json.loads(_read_file(jp)).get("jobs") or []
            except Exception:
                rep.issue(f"{persona}/cron", "jobs.json unreadable")
                continue
            pinned = [j for j in jobs if j.get("model") is not None or j.get("provider") is not None]
            if pinned:
                for j in pinned:
                    rep.note(f"{persona}/cron", f"job '{j.get('name')}' pins model={j.get('model')!r} provider={j.get('provider')!r} (allowed per-job override)")
            else:
                rep.note(f"{persona}/cron", f"{len(jobs)} job(s) rely on the persona config (model=null)")

    return n, runs, live, multiplex_target

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["apply", "status"])
    ap.add_argument("--home", required=True, help="instance hermes-data directory")
    ap.add_argument("--launch-profile", default="default", help="profile the compose service launches (its config carries multiplex/backends)")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--defaults", default=DEFAULTS_CANDIDATES)
    ap.add_argument("--uid", type=int, default=None)
    ap.add_argument("--gid", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    home = args.home.rstrip("/")
    if not os.path.isdir(home):
        sys.exit(f"error: not a directory: {home}")
    defaults = _load_yaml(args.defaults)

    base_url = args.base_url.rstrip("/")
    if not base_url.endswith((defaults.get("endpoint") or {}).get("path", "/v1")):
        base_url = f"{base_url}{(defaults.get('endpoint') or {}).get('path', '/v1')}"

    rep = Reporter(quiet=args.quiet)
    repo = f"launch={args.launch_profile}"
    n, runs, live, mp = process(home, defaults, base_url, args.api_key,
                                args.launch_profile, args.mode, rep,
                                uid=args.uid, gid=args.gid)
    if not args.quiet and not args.json:
        print(f"personas: {n} ({repo}); backends target: {runs} concurrent run(s), {live} live session(s)"
              + ("; multiplex on (n>1)" if mp else "; single persona, multiplex not enforced"))
    rep.dump(json_mode=args.json)
    if args.mode == "status":
        sys.exit(1 if rep.issues else 0)

if __name__ == "__main__":
    main()
