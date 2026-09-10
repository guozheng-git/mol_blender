# Your own cluster

Each lab owns its worker and optional HTTPS gateway. Lab inputs do not pass through the shared Modal service.

On the cluster, unpack the release source distribution into a shared directory and install the service in Python 3.11. Download the versioned SIF and verify its published SHA256, or build it from the repository root:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install '.[server,mcp]'
apptainer build blender-render-0.2.0b1-linux-x64.sif blender_render/blender_bridge.def
```

The build pins the base image by digest, downloads Blender by SHA256, installs hash-locked Python dependencies, and verifies the CCD snapshot. `/opt/notices/debian-packages.tsv` records the installed OS packages. See [dependency notices](../THIRD_PARTY_NOTICES.md) for sources and licenses.

Copy `blender_render/profile_slurm.json` for standard sbatch, or `blender_render/profile_ipd.json` for IPD's explicit qlogin launcher. Version-2 profiles require `launcher.kind` and a `launcher.arguments` argv array for partition, GPU, memory, CPUs and time. Legacy profiles must be migrated explicitly; a missing launcher never selects one automatically. Set the SSH host alias, shared source directory, Python interpreter, SIF path and session registry. Do not commit personal paths or tokens.

```sh
blender-render profile add ipd --file my-ipd.json
blender-render service start --profile ipd
blender-render render examples/1sps_pose.pse --profile ipd --transparent --save-blend -o figure.png
blender-render service status --profile ipd
blender-render service stop --profile ipd
```

In PyMOL:

```python
blender_render mode=interactive, profile=ipd
blender_render mode=interactive, profile=ipd, attach=1
blender_render mode=interactive, profile=ipd, attach_only=1
```

Render execution first admits a durable operation, then starts the configured allocation and loopback service. The IPD template includes `--gres=gpu:small:1 --constraint=A4000`; verify site resource names during explicit setup. A private registry records allocation/session identity, cluster when supplied by sbatch, node, port, separate consumer/controller credentials and health observations. Unknown submission outcomes retain a fence and require scheduler inspection. `service attach` and `attach_only=1` never allocate. `doctor --profile` uses configuration/cached data only; status reads do not repair connections. Exact engine identity is checked before allocation and again against the loaded worker before dispatch.

Multiple clients attach to the same FIFO queue. Status includes the session ID, submitter, position, and timestamps. The worker reuses one Blender process, supports cancellation, and stops after 15 idle minutes. An interrupted running job fails explicitly and is not silently replayed. Queued jobs survive a service restart.

Gateway transfer recovery retains the child operation and completed output. It resumes verified transfers and acknowledgements without rerendering. See [C3 execution and recovery](c3-execution.md) for immutable snapshots, diagnostics and the `recover-transfer` endpoint.

The default transport uses SSH to the login host plus `srun --jobid ... --overlap` for a loopback proxy inside the allocation. This works at sites that prohibit direct SSH to GPU nodes. Set `transport` to `ssh` where a ProxyJump connection to the allocated node is permitted. Do not expose the worker's port publicly.

## ChatGPT through a lab gateway

Run the gateway on a lab-owned Linux host with SSH access to the cluster, Apptainer isolation support, and a TLS reverse proxy such as Caddy. Create a GitHub OAuth app with callback `https://render.your-lab.example/auth/callback`. Store its client ID/secret plus four independent, randomly generated keys in a mode-0600 JSON file. The key names are:

```json
{
  "GITHUB_CLIENT_ID": "your application ID",
  "GITHUB_CLIENT_SECRET": "your application secret",
  "BLENDER_RENDER_ADMIN_TOKEN": "at least 32 random characters",
  "BLENDER_RENDER_DOWNLOAD_KEY": "at least 32 random characters",
  "BLENDER_RENDER_ENCRYPTION_KEY": "at least 32 random characters",
  "BLENDER_RENDER_JWT_KEY": "at least 32 random characters"
}
```

```sh
blender-render gateway --profile ipd --public-url https://render.your-lab.example \
  --credentials /private/path/credentials.json --allowed-user GITHUB_NUMERIC_USER_ID \
  --converter-image /shared/path/blender-render-0.2.0b1-linux-x64.sif
```

Repeat `--allowed-user` for each lab member. The gateway binds to `127.0.0.1:8766`; forward your HTTPS hostname to it. Connect ChatGPT to `https://render.your-lab.example/mcp`. The gateway keeps its own owner-isolated queue and encrypted OAuth store and forwards jobs to the named HPC profile. Its download links expire after 15 minutes.

PSE conversion requires a separate Apptainer process with no network, no host home, no credentials, a private input/output directory, and a 90-second lifetime. If the host cannot provide those isolation features, conversion fails. Desktop users can still export their own trusted PSE locally through the stdio adapter and submit validated structure data.

Run only one gateway process per state directory. Drain it before upgrades. Configure your reverse proxy to accept at most 50 MiB per request, rate-limit connections, and terminate TLS. Publish your lab's retention and support policy for its users.
