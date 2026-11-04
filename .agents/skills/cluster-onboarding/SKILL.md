---
name: cluster-onboarding
description: Bring the optional Blender Render cluster service up on a new Slurm + Apptainer cluster - discover the site's partition, account and GPU request syntax, write server.json, run cluster doctor, then the real Slurm acceptance script. Use when an administrator asks to set up, port or troubleshoot the shared cluster renderer on a specific cluster, not for ordinary local rendering.
---

Configure the existing cluster service for one site. Never change renderer or
launcher code to fit a site; everything site-specific belongs in `server.json`.
If a site needs something the configuration cannot express, stop and report it.
Read [shared cluster setup](../../../docs/shared-cluster.md) first.

## 0. Check the site is supported

The service supports **Slurm**, **Apptainer 1.1 or newer**, **Linux x86_64**
and zero or one **NVIDIA CUDA** GPU per job. Confirm before writing anything:

```sh
sinfo --version
apptainer --version          # or: singularity --version
uname -m                     # must be x86_64
```

- PBS, LSF, Grid Engine, HTCondor or Kubernetes: not supported. Say so.
- SingularityCE or legacy Singularity: `cluster doctor` only warns. Treat it as
  untested until `validate_cluster_slurm.py` passes, and record the result.
- Apptainer older than 1.1: ask the site to provide a newer module or install.
- ARM nodes (Grace Hopper, Grace): not supported by the image.
- **AMD GPUs:** Blender's AMD (HIP) rendering supports only RDNA Radeon cards.
  Instinct MI-series accelerators, which AMD clusters usually have, are not
  supported, and this service only requests CUDA. Use those nodes for CPU
  rendering with `"gpus": 0`. Do not attempt a HIP configuration.

Also confirm the gateway host is allowed to run a long-lived service (many
centers forbid daemons on login nodes), has the Slurm client tools, and shares
the spool and SIF paths with compute nodes at the same absolute paths.

## 1. Discover the site's Slurm values

Prefer the site's own documentation; use these commands to confirm it:

```sh
sinfo -o "%P %a %l %D %G %f"          # partitions, time limit, GRES, node features
scontrol show partition PARTITION      # MaxTime, AllowAccounts, AllowQos, defaults
sacctmgr -n show assoc user=$USER format=account,partition,qos
scontrol show node NODE | grep -E 'Gres|AvailableFeatures'
```

Map the results to `server.json`:

| Observation | Setting |
| --- | --- |
| GRES column shows `gpu:N` | `"gpu_request": "gres"`, no `gpu_type` |
| GRES shows typed GPUs, e.g. `gpu:a100:4`, and docs require the type | `"gpu_request": "gres"`, `"gpu_type": "a100"` |
| Docs use `--gpus=1` or `-G 1` | `"gpu_request": "gpus"` (optionally `gpu_type`) |
| Docs select GPU models through `--constraint` / features | keep `gres`, set `"constraint": "a100"` or `"a100\|h100"` |
| A partition, account or QoS is required | `partition`, `account`, `qos` |

The resulting job request is exactly one of `--gres=gpu:1`, `--gres=gpu:TYPE:1`,
`--gpus=1` or `--gpus=TYPE:1`, plus `--constraint=...` if set. `constraint` also
applies to CPU jobs. Only letters, digits, `_`, `.`, `-` (and `&`/`|` between
constraint features) are accepted; bracketed counts and multi-GPU requests are not.

Check the request by hand before writing the configuration, replacing the
values with the site's:

```sh
sbatch --test-only --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=8192M \
  --time=01:05:00 --partition=gpu --gres=gpu:1 --wrap=true
```

`--test-only` validates and estimates a start time without queuing a job.

## 2. Write `server.json`

Start from [`examples/cluster-server.json`](../../../examples/cluster-server.json).
Use absolute paths found with `command -v sbatch` etc.; module-provided tools
often live outside `/usr/bin`. Commands run with `PATH` set to those tools'
directories plus `/usr/bin:/bin` and no `LD_LIBRARY_PATH`, so check that each tool
runs from a clean shell (`env -i PATH=<dir>:/usr/bin:/bin <tool> --version`).
Create the private spool (mode `0700`) before running doctor. Keep `walltime_seconds` at least 60 seconds above
`limits.render_timeout` and below the partition's MaxTime. Start with a CPU
configuration (`"gpus": 0`); add the GPU settings after CPU acceptance passes.
Unknown fields are rejected, which catches typos.

## 3. Run `cluster doctor`

```sh
blender-render cluster doctor --config /path/to/server.json
```

It never installs, queues or renders. It checks, in order: configuration, SIF
checksum, executables, Apptainer version, the container identity check with the
real job flags, and `sbatch --test-only` with the real job options. Read
`warnings`, `apptainer` and `slurm_dry_run` in its report. `render_verified` is
always `false`: doctor runs on the gateway, not on a compute node.

## 4. Run real acceptance

On the gateway, with a NEW output directory on shared storage:

```sh
python scripts/validate_cluster_slurm.py --server-config /path/to/server.json \
  --pymol /path/to/pymol/bin/python --fixture examples/1stp.cif \
  --skill dist/cluster-release/plugin/structure-studio/skills/structure-studio \
  --output /shared/acceptance/cpu
```

Run CPU first, then the GPU configuration (new `--output`). It submits real jobs
and records the compute node's Apptainer version, the actual Cycles device and
Slurm accounting in `evidence.json`. Only `"passed": true` there establishes the
site works. Record the outcome, versions and GPU model in `docs/validation.md`
without private hostnames, accounts or paths.

## Troubleshooting

| Symptom | Likely cause and action |
| --- | --- |
| `Apptainer X is too old` | Load a newer Apptainer module and point `apptainer` at it. |
| `Slurm dry run failed: Invalid generic resource` | Wrong GPU syntax: try a `gpu_type`, `"gpu_request": "gpus"`, or a `constraint`. |
| Dry run rejects partition/account/QoS or time limit | Fix those values from `scontrol show partition` and `sacctmgr`. |
| Dry run passes, real submission rejected | A site `job_submit` plugin; read the private job's diagnostics and site docs. |
| `error while loading shared libraries` or `not found` in a doctor or dry-run error, while the tool works in your shell | Commands run with only the configured tool directories plus `/usr/bin:/bin` on `PATH` and no `LD_LIBRARY_PATH`. The tool needs its module's environment: point the config at a wrapper script or an RPATH install. |
| `--no-mount` / bind errors from the container check | Apptainer or `apptainer.conf` lacks the needed features; ask the site admins. |
| `GLIBC_x.yy not found` loading NVIDIA libraries | Host drivers need a newer glibc than the image; report it, a rebuilt base image is required. |
| `Slurm did not assign a GPU` in the job log | GPU request accepted but no device granted to the step; check GRES configuration with the site. |
| Jobs stuck or spool lock errors | The spool filesystem lacks reliable POSIX locking (some NFS); use another shared filesystem. |
| Build fails under fakeroot | Use an administrator-supported build, e.g. authorized `sudo apptainer build` on local scratch. |
