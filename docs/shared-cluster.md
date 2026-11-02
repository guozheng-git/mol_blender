# Optional shared cluster rendering

The same renderer can run locally or through a shared cluster service:

```text
PyMOL / CLI → HTTPS gateway → Slurm → Apptainer → existing local renderer
                 ↑                                  ↓
                 └──── status and verified downloads ┘
```

The gateway runs on a cluster service host with Slurm client tools. It accepts
exported structure data and validated styles, submits one job, and serves the
results. Token authentication gives each user a private queue. An administrator
can explicitly select open access for a trusted cluster network, with one shared
queue and no credentials. It never renders in the web process. Local `blender_render`
and `blender-render render` keep their existing behavior unless a cluster is
explicitly selected. The optional stdio MCP and hosted image-delivery adapter
remain separate interfaces.

## Use from PyMOL

Install the base package in a local uv environment and run `blender-render setup`
there to record its Python interpreter. A local Blender installation is not
required for remote submission from an already running PyMOL. The administrator
provides either an open service URL or a private credential JSON file. For an
open cluster, configure the URL once:

```sh
blender-render cluster configure --profile lab --url https://render.cluster.example:8772
blender-render cluster info --profile lab
```

Add `--ca-file /shared/render-ca.pem` if the service uses a private CA. There is
no token or login in open mode. Everyone who can reach the endpoint can list,
fetch, cancel and delete jobs in its shared queue. The per-user active-job limit
therefore applies to the whole shared queue, alongside the total service limit.

For a service using individual credentials, import the provided file instead:

```sh
blender-render cluster configure --profile lab --credential-file /private/alice.json
blender-render cluster info --profile lab
```

The file must be owned by you and mode `0600`. The client stores a private profile
under `BLENDER_RENDER_HOME/clusters/` (or the ordinary user configuration root).
Delete the transfer copy after importing it. Tokens never appear in command-line
arguments or normal output. Use `--ca-file` with `configure` for a private CA;
certificate verification cannot be disabled. HTTPS is required except for a
loopback URL reached through an SSH tunnel. The client refuses redirects and
does not use environment-configured HTTP proxies.

On the server, open access requires `"authentication": "none"` in its configuration;
`tokens_file` can then be omitted. The default is `"authentication": "token"` and
requires a private credential database. Keep an open service bound to the cluster
network address. TLS, input validation, resource limits and Slurm isolation apply
in both modes. All jobs use the gateway's Unix identity and configured Slurm
account, including jobs submitted by other cluster users.

Load `blender_render/pymol_bridge_plugin.py` as described in the README, then:

```pymol
blender_render cluster=lab, preset=mint_sky_assembly, output_dir=renders, output_name=protein.png
blender_render_status JOB_ID, cluster=lab
blender_render_fetch JOB_ID, cluster=lab, output_dir=renders, output_name=protein.png
blender_render_cancel JOB_ID, cluster=lab
```

Submission exports the current enabled selection, transforms, colors, secondary
structure and view, then returns a job ID and a local `.cluster.json` recovery
receipt. Uploading takes time, but PyMOL does not wait through the queue or render
by default. Fetch downloads and checks every artifact before loading the PNG or
animation into PyMOL. Use `wait=1, timeout=600` to wait and fetch in one command;
this blocks PyMOL, and the timeout includes queue time. A wait timeout leaves the
original job running and never submits another one.

Animation controls work as they do locally:

```pymol
blender_render cluster=lab, animation=rock, duration=2, fps=12, gif=1, width=640, height=480, samples=16, output_name=rock.png
```

Omit `cluster=lab` to render locally. Custom styles and saved local presets are
resolved on the submitting computer and sent as complete validated JSON. Server
resource policy chooses CPU or CUDA; a GPU allocation requires an actual GPU and
does not silently fall back to CPU.

## CLI submission and recovery

```sh
blender-render cluster submit structure.cif --profile lab --rough -o renders/first.png
blender-render cluster status renders/first.cluster.json
blender-render cluster fetch renders/first.cluster.json
blender-render cluster list --profile lab
blender-render cluster delete JOB_ID --profile lab
```

CLI imports of CIF/PDB/PSE require the configured local PyMOL interpreter. An
exported bundle JSON does not. A PSE is loaded only on the submitting computer:
it must be trusted because loading it can execute code. The service accepts no
PSE, Python, Blender scene uploads, arbitrary file paths or user-provided shell
commands. Only data exported from the session crosses the network.

The receipt is written before upload and contains recovery identity, not tokens
or molecular coordinates. After a lost response, query that receipt even if it
has no job ID yet. If the service reports an unknown submission, ask its
administrator to reconcile it; do not create a replacement job speculatively.
An explicit rejection or a confirmed missing request can be submitted again with
a new output name. Downloads never overwrite existing outputs. A failed integrity
check leaves no partial render in the requested output location.

## Build once, reuse the uv environments

Use Linux x86_64. From a clean, committed checkout, build the wheel and staged
plugin from one revision, then build the container from those exact artifacts:

```sh
uv sync --locked --extra dev --extra cluster
uv run --no-sync python scripts/build_release.py --output dist/cluster-release
apptainer build --build-arg RELEASE_DIR=dist/cluster-release renderer.sif containers/Apptainer.def
sha256sum renderer.sif
```

The image calls the existing `studio.py setup` once during its explicit build.
That uses uv, Python **3.11.15**, and the same hash-locked render/PyMOL requirements
used locally. The renderer wheel is the one just built from the committed source.
Each Slurm job directly executes `/opt/studio-runtime/render/bin/python` in that
image. Jobs never run `uv sync`, pip, installers or dependency resolution.
The PyMOL and Blender environments remain separate because they have different
binary dependencies. Nothing copies or relocates a Mac virtual environment.

For an application-only update to an already validated Linux SIF, first verify
the base image against its recorded SHA-256. Then use
[`Apptainer-update.def`](../containers/Apptainer-update.def) with `BASE_IMAGE` and
`RELEASE_DIR` build arguments. It requires identical render/PyMOL requirement
files and runs the same uv setup offline, keeping the Linux environments at their
original absolute paths. Changed dependency pins require a full image build.
Record the new SIF hash and run `cluster doctor` before switching the gateway;
gateway and image must contain the same wheel.

You can also **reuse an existing Linux Studio uv runtime**. Add this administrator
setting to the server config:

```json
"runtime_home": "/shared/blender-runtime"
```

The gateway binds this directory read-only **at the same absolute path** inside
Apptainer and uses its `render/bin/python` and `blender-render/config.json`.
It must be a complete Linux runtime created with the same Studio setup and pins,
including uv's managed Python beneath that directory. Install the exact release
wheel there before serving. Its configured Blender shim, dependencies and CCD
must all be reachable through that bind. Do not move a venv or bind a Mac venv
into Linux. Run `cluster doctor` to check the interpreter, configured files and
renderer identity inside the container. The worker also refuses a gateway/image
source mismatch before rendering; ordinary render-time dependency probes still
apply. A compatible existing Linux environment is reusable locally and through
the container without installing anything per job.

The SIF supplies Linux libraries and FFmpeg in either mode. Host GPU drivers are
exposed with `--nv`. The pinned Debian 13 base supplies glibc 2.41; the original
Debian 12 base could not load the target compute nodes' newer graphics libraries.
Other clusters still need their own real CUDA check because `--nv` binds host
libraries into the image. The base-image digest and Python dependencies are pinned;
Debian repository packages are not snapshot-pinned, so byte-identical OS builds
are not claimed. See [runtime environments](runtime-environments.md).

On hosts without subordinate UID/GID mappings, Apptainer may bind the host's
`fakeroot` into the build. A newer host glibc can make that fail inside the pinned
Debian image. Use an administrator-supported build method, such as an authorized
`sudo apptainer build` on local scratch storage; root-squashed NFS directories may
be unreadable to that build. This does not require running render jobs as root.
See [Apptainer's fakeroot compatibility guidance](https://apptainer.org/docs/user/latest/fakeroot.html).
An optional `--build-arg BUILD_PROXY=http://127.0.0.1:PORT` supplies a dependency
proxy during the build only. It does not configure a proxy for render jobs.

## Configure the service

Use a dedicated, unprivileged cluster account. Install the gateway from the same
revision with `uv sync --locked --extra cluster`, or install the built wheel with
its `cluster` extra in an isolated environment. Copy
[the configuration example](../examples/cluster-server.json) to a private
administrator-controlled location and adapt:

- Shared spool and SIF paths visible at the same paths on the gateway and compute
  nodes. The spool must be owned by the service account, mode `0700`, and support
  reliable POSIX file locking. Use a filesystem quota for a hard disk limit.
- The SHA-256 of the built SIF and absolute paths to the Slurm/Apptainer tools.
- Partition, account, QoS, CPUs, memory, walltime and zero or one GPU per job.
  For CUDA set `gpus` to `1`. The Slurm walltime must exceed the renderer timeout
  by at least 60 seconds.
- Limits for uploads, image size, samples, frames, active jobs and retention.
  Defaults accept at most 20 MiB of uncompressed JSON, 16 megapixels, 512 samples,
  360 frames, four active jobs per user, and 32 active jobs overall. The existing
  animation memory/archive limits also apply. Individual downloads are capped at
  1 GiB. The spool-byte setting is an admission check, not a filesystem quota.

Create the spool and private credential directory, then issue credentials:

```sh
blender-render cluster user-add --config /srv/blender-render/server.json \
  --user alice --url https://render.example.org \
  --credential-output /private/alice.json
blender-render cluster doctor --config /srv/blender-render/server.json
blender-render cluster serve --config /srv/blender-render/server.json
```

`user-add` writes a random token to the private transfer file; the service stores
only its SHA-256 digest. Deliver the file privately to its owner. Use
`user-revoke --config ... --user alice` to revoke access immediately, then issue a
new credential if needed. Revocation does not cancel already submitted jobs.

The service defaults to `127.0.0.1:8765`. Put an HTTPS reverse proxy in front, or
use SSH forwarding to that loopback address. Direct TLS is available with
`serve --bind ... --tls-cert ... --tls-key ...`. Non-loopback plaintext serving
requires explicit `--behind-proxy`; restrict that listener to the proxy. Configure
the proxy's upload limit and timeouts to match the service. No deployment, firewall
change or external listener is created by installing the package.

All Slurm jobs run as the **service Unix account**, using the configured Slurm
account. API identities provide ownership and quotas; they do not impersonate
users' Unix accounts or create separate Slurm billing identities. This is intended
for an authenticated, trusted cluster community. Apptainer containment and private
job binds reduce exposure but are not a sandbox for arbitrary uploaded programs.
For mutually untrusted tenants or per-user Unix accounting, add an administrator-
approved identity/batch submission mechanism before deployment.

Only one job directory is bound writable into each worker; the token database and
other jobs are not bound. `srun` supplies CPU affinity and GPU allocation, and the
launcher forwards its `CUDA_VISIBLE_DEVICES` through the clean environment. Slurm
must enforce the site's CPU, memory and device limits. SIF/runtime directories
should be read-only to service clients and immutable while jobs are active.
The isolated renderer retains these compute limits and Apptainer's fixed driver
library path, while discarding unrelated library paths and credentials.

## Persistent user service and shared clients

[`cluster-user.service`](../examples/cluster-user.service) is a systemd user-unit
template. Put a uv controller environment at `~/blender-render-cluster/controller`,
created at that final path using the pinned Linux Python. Export controller
dependencies from `uv.lock` with `uv export --frozen --no-dev --extra cluster
--no-emit-project`, install them with `uv pip sync --require-hashes`, and install
the release wheel with `uv pip install --no-deps`. The service does no installation
at startup or per job.

Keep `server.json`, `server.crt` and `server.key` under the private
`~/.config/blender-render-cluster` directory. Keep the spool private and visible
on compute nodes. Set `authentication` to `none` only for an intentionally shared
cluster queue. Choose the appropriate Slurm account/partition and bounded active
job limits; open callers all count toward `max_active_per_user`.

Replace `@CLUSTER_ADDRESS@` and `@PORT@` in the unit with an available cluster
interface address and free port, and install it as
`~/.config/systemd/user/blender-render-cluster.service`. Supply a certificate
whose SANs match the service hostname/IP. For a private CA, share only its public
certificate as `~/blender-render-cluster/ca.pem`; keep both private keys private.
Track certificate expiry and renew the leaf before it expires.

```sh
systemctl --user daemon-reload
systemctl --user enable --now blender-render-cluster.service
systemctl --user status blender-render-cluster.service
journalctl --user -u blender-render-cluster.service
loginctl show-user "$USER" -p Linger
```

The unit restarts after failures. `Linger=yes` is needed to keep the user manager
running after logout and start it at boot; if disabled, the administrator can
enable it with `loginctl enable-linger USER`. See the upstream
[service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
and [linger](https://www.freedesktop.org/software/systemd/man/latest/loginctl.html)
documentation. Check a real restart and a compute-node connection before
advertising the endpoint. A configured boot policy is not a tested reboot.

For users sharing the Linux filesystem, the controller and its uv-managed Python
must be readable/executable by those users and writable only by the administrator.
Copy [`cluster-connect.sh`](../examples/cluster-connect.sh) next to `controller`
and `ca.pem`, replacing `@SERVICE_URL@` with the HTTPS origin. Each colleague runs
that script once, optionally passing a profile name (default `lab`). It writes
their own profile and reports the `run .../pymol_bridge_plugin.py` command to use
inside PyMOL. It installs nothing into their PyMOL or system Python, and preserves
their configured local Blender/PyMOL paths. Users on another computer can install
the release wheel into their own uv environment and use the URL/CA configuration
shown above.

Stop only this service with `systemctl --user stop blender-render-cluster.service`;
disable boot startup with `systemctl --user disable blender-render-cluster.service`.
Stopping the gateway does not cancel already submitted Slurm jobs. For an update,
finish active jobs, stage a new immutable image/controller with matching identities,
update the configuration and unit, then restart. Keep the previous release for rollback.

## Lifecycle and administrator recovery

Jobs are durable across gateway restarts. Each request ID can submit only once
per API user; resending different input under that ID fails. If `sbatch` times out
or the gateway exits after submitting, the service reports uncertainty rather than
retrying. Reconcile using the exact Slurm job name and service Unix owner:

```sh
blender-render cluster reconcile JOB_ID --config /srv/blender-render/server.json
```

Reconciliation attaches an existing allocation. It never submits a new job. If
accounting is unavailable or no allocation can be established, the job stays
uncertain for administrator investigation. A Slurm requeue also cannot silently
rerender an already started job.

Completed results are available only after the worker records their sizes and
hashes. Scheduler completion without a verified worker result is failure. Cancel
is asynchronous; deletion waits for a terminal state. Inputs are removed when the
worker finishes. A background sweep deletes remaining job data and artifacts after
the retention period (24 hours by default), including failed jobs. A small ownership
and request-hash tombstone remains to prevent expired requests from being rerun.
User-initiated deletion removes artifacts sooner. Private worker/Slurm logs remain
in the job directory until cleanup; the API does not expose those logs.

## Validation and upstream behavior

Run source and installed-wheel tests with the `dev`, `mcp` and `cluster` extras.
Before opening a service to other users, exercise a real Slurm allocation, GPU
selection if enabled, cancellation, image/movie download and cleanup on the target
cluster. Local HTTP tests do not establish Slurm or Apptainer integration. The
current executed checks and outstanding gates are in [validation](validation.md).

For reproducible real acceptance, run this on the Linux gateway with the installed
wheel or the source environment. The output directory must be new and visible on
compute nodes at the same absolute path:

```sh
python scripts/validate_cluster_slurm.py \
  --server-config /private/acceptance-server.json \
  --pymol /shared/blender-runtime/pymol/bin/python \
  --fixture examples/1stp.cif \
  --skill dist/cluster-release/plugin/structure-studio/skills/structure-studio \
  --output /shared/acceptance/cpu
```

The script uses the configured image and resource policy, but creates its own
spool, loopback gateway and short-lived credentials. It submits two small renders
and one cancellation probe. Checks include PyMOL submission, original-request
recovery, actual CPU/CUDA selection, checksummed downloads, saved-scene reopening,
movie decoding, frame and session bundle restoration, per-user ownership,
idempotency, revocation and deletion. Failures retain private diagnostic logs and
request cancellation of any outstanding test allocations. `evidence.json` records
whether the run passed. Repeat with a GPU configuration if GPU service is enabled,
and with `runtime_home` set to validate reuse of an existing Linux uv runtime.
Add `--cancel-running` to exercise cancellation after Slurm starts the probe;
this requests a longer animation and cancels it as soon as running is observed.

The launcher follows the documented behavior of
[Slurm sbatch](https://slurm.schedmd.com/sbatch.html) (`--parsable`, `--export=NIL`),
[Slurm sacct](https://slurm.schedmd.com/sacct.html),
[Apptainer binds](https://apptainer.org/docs/user/latest/bind_paths_and_mounts.html),
[Apptainer environment overrides](https://apptainer.org/docs/user/latest/environment_and_metadata.html),
[Apptainer NVIDIA support](https://apptainer.org/docs/user/latest/gpu.html),
and [uv container environments](https://docs.astral.sh/uv/guides/integration/docker/).
