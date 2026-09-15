# Hermes Spawning

`hermes-spawning` creates independent, persistent pairs of
[Nous Research Hermes Agent](https://github.com/NousResearch/hermes-agent) and
[Tailscale](https://tailscale.com/docs/features/containers/docker). Each pair
has its own identity, state, resource limits, credentials, and Docker Compose
project.

## Quick start

```bash
cd /root/hermes-spawning
./hermes-spawn.sh spawn
```

The wizard asks for:

1. A Tailscale hostname (also the local instance name).
2. The Hermes profile that should be active by default.
3. CPU and memory limits, prefilled with generous host-aware defaults.
4. A Tailscale auth key. If a reusable key is installed as described below,
   the launcher reads it automatically; otherwise it prompts securely.

It then launches the official Hermes setup wizard so you can choose the model
provider, credentials, messaging channels, tools, and the rest of the agent
configuration. Secrets are entered into that upstream wizard rather than into
this project.

## Administrator: reusable Tailscale enrollment key

A Tailnet administrator can create one tagged, reusable auth key for spawning
multiple instances:

1. Open <https://login.tailscale.com/admin/settings/keys> and generate an auth
   key with **Reusable** enabled. Apply the narrowest device tag and practical
   expiry allowed by your Tailnet policy.
2. Copy `.tailscale-authkey.example` to `.tailscale-authkey`, replace its
   contents with only the real `tskey-...` value, and restrict access:

   ```bash
   cp .tailscale-authkey.example .tailscale-authkey
   chmod 600 .tailscale-authkey
   ```

3. Run `./hermes-spawn.sh spawn` or `./hermes-spawn.sh reauth HOSTNAME`. The
   launcher automatically uses the shared key. Its per-instance copy is
   emptied after the node is online; the shared reusable key remains for the
   next instance.

The real `.tailscale-authkey` is intentionally ignored by Git. Treat it as a
high-value Tailnet credential: never commit, paste into logs, or distribute it
with a repository archive. Revoke and replace it immediately if exposed.

Useful commands:

```bash
./hermes-spawn.sh list
./hermes-spawn.sh status HOSTNAME
./hermes-spawn.sh setup HOSTNAME
./hermes-spawn.sh chat HOSTNAME
./hermes-spawn.sh shell HOSTNAME
./hermes-spawn.sh logs HOSTNAME
./hermes-spawn.sh credentials HOSTNAME
./hermes-spawn.sh reauth HOSTNAME
./hermes-spawn.sh update HOSTNAME
./hermes-spawn.sh stop HOSTNAME
```

Instance files live in `instances/HOSTNAME/`. Back up `hermes-data/` to retain
the agent configuration, sessions, memories, skills, and profile state. Back up
`tailscale-state/` only if you also want to retain the Tailnet node identity.

## Network and containment model

There are no published Docker ports. Hermes joins the Tailscale container's
network namespace and listens only on `127.0.0.1` there. Tailscale Serve proxies
the dashboard as private HTTPS on port 443 and the authenticated OpenAI-style
API as private HTTPS on port 8443. Tailscale Serve does not enable Funnel, so
these endpoints are not public internet endpoints.

The generated API key is shown by `credentials`. The dashboard relies on
Tailnet identity and ACLs plus a generated Hermes dashboard login; both the
login and the Hermes Desktop/API connection details are shown by
`credentials`. Restrict access to each node in the Tailscale policy to the
users or device tags that should control that Hermes instance.

For Hermes Desktop, use the `API base` and `API key` printed by
`./hermes-spawn.sh credentials HOSTNAME`. For the browser dashboard, use the
printed `Dashboard`, `Web user`, and `Web pass` values and choose **Sign in
with Username & Password**.

Hermes receives only its instance's `hermes-data/` bind mount. It does not get
the Docker socket, `/root`, the host network, host PID namespace, or privileged
mode. Linux capabilities are dropped except for the small set the official
image needs during startup to switch from its supervisor to the unprivileged
`hermes` user. Tailscale receives only `NET_ADMIN`, `NET_RAW`, `/dev/net/tun`,
and its own state directory. Both services have process, memory, CPU, log-size,
and restart controls.

This is strong whole-process container isolation, not a claim of mathematical
or VM-grade security. Anyone controlling Docker on the host, the Tailnet admin
plane, a configured model/provider credential, or a trusted Hermes plugin is
inside part of the trust boundary. For hostile multi-tenant workloads, put each
pair in its own VM as an additional boundary.

## Operational notes

- The auth-key file is emptied after the first successful Tailscale login;
  persisted node state handles later restarts. If that identity is revoked or
  expires, use `reauth` to enroll it again without disturbing Hermes data.
- Tailscale HTTPS certificates must be enabled for the tailnet. If the first
  Serve attempt prints an enablement URL, follow it and run
  `./hermes-spawn.sh serve HOSTNAME` again.
- `update` pulls the current official image tags. Inspect and pin
  `HERMES_IMAGE` and `TAILSCALE_IMAGE` in an instance's `control.env` if your
  change-control policy requires immutable image digests.
- Do not run two Hermes containers against the same `hermes-data/` directory.
- Hermes is intentionally not given the host Docker socket. Its local terminal
  tools already execute inside the contained Hermes filesystem; mounting the
  socket would effectively grant it host-root-equivalent control.

## Files

- `compose.yaml` — hardened two-container template shared by every instance.
- `hermes-spawn.sh` — provisioning and lifecycle interface.
- `.tailscale-authkey.example` — non-secret template for the administrator.
- `.tailscale-authkey` — ignored mode-`0600` reusable enrollment credential.
- `instances/HOSTNAME/control.env` — generated Compose controls (mode `0600`).
- `instances/HOSTNAME/system-info.txt` — host capacity/security snapshot.
- `instances/HOSTNAME/hermes-data/` — all mutable Hermes state.
- `instances/HOSTNAME/tailscale-state/` — persistent Tailscale node state.
