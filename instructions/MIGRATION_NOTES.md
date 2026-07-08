# Migration / operational notes

Operational gotchas specific to running this project's tooling from a
**Windows dev machine** against the Railway-hosted production deployment.
Read this before running any script that touches `/data` (the SQLite DB or
image storage) on Railway.

---

## `railway run` does NOT reach production `/data` — use `railway ssh`

**`railway run <command>` executes the command LOCALLY.** It only injects
Railway's environment variables into the local process (per its own
`--help` text: "Run a local command using variables from the active
environment"). It does **not** run inside the deployed container and does
**not** have access to Railway's mounted volume.

This project's production `DATABASE_PATH` is `/data/app.db` and
`IMAGE_STORAGE_ROOT` is `/data/images` — both paths inside a Railway
**volume**, which only exists inside the deployed container. When
`railway run` injects `DATABASE_PATH=/data/app.db` into a process running
on Windows, Windows path resolution reinterprets the leading `/` as the
current drive's root — so the path silently becomes **`C:\data\app.db`**,
a local file, instead of erroring out. Any script run this way will
happily read/write that local file, print success, and have touched
nothing on the real production database.

**Confirmed empirically (2026-07-08):** a local `C:\data\app.db` exists,
created the same day `cleanup_test_data.py` was first run via
`railway run`, with a completely empty `tasks` table. A "VERDICT: clean"
result from that run was real for that file, but meaningless for
production — the real production DB was independently re-checked via
`railway ssh` and happened to also be clean, but that had to be verified
separately; it cannot be assumed from a `railway run` result.

**The only verified-correct method from this machine is `railway ssh`**,
which opens a shell inside the actual running container (`/data/app.db`
resolves correctly there, confirmed via `data_dir resolved to: /data` and
direct `os.environ` reads matching `railway variables`).

### Setting up `railway ssh` (one-time)

```
railway ssh keys add -k <path-to-existing-public-key>   # via PowerShell if Git Bash path mangling fails
```
Registers a local SSH public key with your Railway account. If the first
connection fails with "Host key verification failed" (not a MITM warning —
just first-contact strict host key checking with no TTY to prompt),
pre-accept Railway's SSH gateway key once:
```
ssh-keyscan -t ed25519 ssh.railway.com >> ~/.ssh/known_hosts
```
`ssh.railway.com` is Railway's stable SSH gateway hostname — safe to trust
the same way you'd trust GitHub's or any other well-known service's host
key on first use.

### Running scripts via `railway ssh`

`railway ssh -- "<command>"` runs a single one-off command in the
container's working directory (`/app`), but does **not** know about your
local, uncommitted script files — it can only see what's actually been
deployed. To run a local script that hasn't been pushed yet, transfer its
content over the SSH command itself rather than relying on `git push` +
redeploy for a one-off operation:

```bash
B64=$(base64 -w0 scripts/your_script.py)
railway ssh -- "rm -f /tmp/x.b64"
CHUNK_SIZE=3000
LEN=${#B64}
i=0
while [ $i -lt $LEN ]; do
  CHUNK="${B64:$i:$CHUNK_SIZE}"
  railway ssh -- "printf '%s' '$CHUNK' >> /tmp/x.b64"
  i=$((i+CHUNK_SIZE))
done
railway ssh -- "base64 -d /tmp/x.b64 > /tmp/x.py && cd /app && PYTHONPATH=/app python3 /tmp/x.py"
```

**Chunking is required, not optional, on this machine.** A single
`railway ssh -- "echo <big base64 blob> | base64 -d > file"` with the
whole payload in one command failed silently/with a confusing shell parse
error once the blob got into the ~9000-character range. This is very
likely a Windows `CreateProcess` command-line-length ceiling (~8191 chars)
being hit when Git Bash hands a long argument to the native `railway.exe`
subprocess — not a Railway or SSH-protocol limitation. ~3000-character
chunks per call have worked reliably.

### General rule of thumb

| Need | Use |
|---|---|
| Real production DB/volume read or write | `railway ssh` (chunked transfer if the script isn't already deployed) |
| Reading env var *values* to confirm what's configured | `railway variables` (reads Railway's stored config directly, not local env — safe) or, for extra certainty, print `os.environ` from inside a `railway ssh` session |
| Anything that must reach `/data/app.db` or `/data/images` | `railway ssh` only |
| Running deployed application code against Railway's env vars but only touching things that don't require the volume (e.g. calling an external API with a Railway-stored secret) | `railway run` is fine — the local/remote path confusion only bites things that resolve to `/data/*` |
