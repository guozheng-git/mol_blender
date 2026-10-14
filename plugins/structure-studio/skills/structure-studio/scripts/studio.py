#!/usr/bin/env python3
"""Structure Studio: fetch, inspect, view (Mol*), PyMOL sessions and Blender renders.

Every command prints JSON on stdout; progress goes to stderr. Run with any Python 3.9+.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import artifact_files  # noqa: E402
import scene as scenes  # noqa: E402
import studio_env  # noqa: E402
import structure_io  # noqa: E402

PRESETS = HERE.parent / "references" / "presets.json"
CPU_LONG_EDGE = 1280
CPU_SAMPLES = 64


def output_dir(explicit: str = "") -> Path:
    """Where deliverables go: an explicit folder, the claude.ai outputs folder, or ./studio-output."""
    if explicit:
        path = Path(explicit).expanduser()
    elif Path("/mnt/user-data/outputs").is_dir():
        path = Path("/mnt/user-data/outputs")
    else:
        path = Path.cwd() / "studio-output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def unique(path: Path) -> Path:
    """The renderer never overwrites, so pick the next free name (also avoiding NAME.render/)."""
    candidate, n = path, 2
    while candidate.exists() or candidate.with_suffix(".render").exists():
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return candidate


def renderable(path: str, directory: Path, name_hint: str = "") -> Path:
    """The renderer reads .pse/.cif/.mmcif/.pdb; decompress .gz, rename .ent and name stored .txt copies."""
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() in {".pse", ".cif", ".mmcif", ".pdb", ".json"}:
        return source
    data, name = structure_io.read_structure_bytes(str(source))
    if name_hint and source.suffix.lower() == ".txt":
        name = Path(name_hint).name
    if name.lower().endswith(".ent"):
        name = name[:-4] + ".pdb"
    if name.lower().endswith(".bcif"):
        raise ValueError("Blender rendering needs mmCIF or PDB text, not BinaryCIF")
    target = directory / name
    if not target.exists():
        target.write_bytes(data)
    return target


def catalog() -> list[dict]:
    return json.loads(PRESETS.read_text())


def cmd_setup(args):
    if args.use_existing:
        return studio_env.use_existing(args.use_existing, args.blender_render_home)
    return studio_env.setup(args.only, background=args.background)


def cmd_fetch(args):
    directory = output_dir(args.dir) if args.dir else output_dir() / "structures"
    try:
        return structure_io.fetch(args.identifier, str(directory))
    except structure_io.DownloadBlocked as exc:
        return {"error": "download_blocked", "message": str(exc),
                "advice": "Ask the user to attach the .cif/.pdb file, or to allow files.rcsb.org (or "
                          "alphafold.ebi.ac.uk) under network egress / allowed domains in their settings."}


def cmd_info(args):
    data, name = structure_io.read_structure_bytes(args.structure)
    summary = structure_io.summarize(data, name)
    summary["path"] = str(Path(args.structure).expanduser().resolve())
    return summary


def cmd_presets(args):
    rows = catalog()
    if args.general:
        rows = [r for r in rows if not r["chain_specific"]]
    return rows


def run_pymol_session(extra: list[str]) -> dict:
    python = studio_env.pymol_python()
    result = subprocess.run([str(python), str(HERE / "pymol_session.py"), *extra], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("PyMOL session failed:\n" + (result.stderr or result.stdout)[-3000:])
    return json.loads(result.stdout)


def cmd_session(args, extra):
    directory = output_dir(args.out_dir)
    source = Path(args.structure).expanduser()
    stem = source.name.split(".")[0]
    output = Path(args.output).expanduser() if args.output else directory / f"{stem}-{args.style}.pse"
    command = [str(source), "-o", str(output), "--style", args.style, *extra]
    if args.png:
        command += ["--png", str(output.with_suffix(".png"))]
    return run_pymol_session(command)


def cpu_defaults(args) -> dict:
    """Keep CPU renders to a few minutes unless the user asked for full quality."""
    if args.full_quality or studio_env.doctor()["gpu_likely"] or args.style_json:
        return {}
    preset = next((p for p in catalog() if args.preset in [p["id"], *p.get("aliases", [])]), None)
    if not preset:
        return {}
    width, height, samples = preset["width"], preset["height"], preset["samples"]
    changes = {}
    if args.width is None and args.height is None and max(width, height) > CPU_LONG_EDGE:
        scale = CPU_LONG_EDGE / max(width, height)
        changes["width"], changes["height"] = round(width * scale), round(height * scale)
    if args.samples is None and samples > CPU_SAMPLES:
        changes["samples"] = CPU_SAMPLES
    return changes


def _json_arg(value: str):
    """A JSON object given inline or as a file path (database documents are unwrapped)."""
    if not value:
        return None
    return scenes.load(value)


def _snapshot(directory: str) -> tuple[str, str]:
    """scene/current and scene/camera files saved by `ArtifactData list scene --out_dir DIR`."""
    base = Path(directory).expanduser()
    found = {name: next(iter(base.rglob(f"scene/{name}.json")), None) for name in ("current", "camera")}
    if not found["current"]:
        raise ValueError(f"No scene/current.json under {base}; read the workspace's `scene` collection with out_dir={base}")
    return str(found["current"]), str(found["camera"] or "")


def cmd_render(args):
    executable, env = studio_env.renderer_env()
    directory = output_dir(args.out_dir)
    name_hint = ""
    if args.workspace:
        args.scene, camera_file = _snapshot(args.workspace)
        args.view = args.view or camera_file
        args.save_blend = True
        meta = next(iter(Path(args.workspace).expanduser().rglob("workspace/meta.json")), None)
        if meta:
            name_hint = scenes.load(str(meta)).get("structure", {}).get("filename", "")
    source = renderable(args.structure, directory, name_hint)
    session = None
    if args.scene or args.view or args.fit:
        extra = []
        tag = "view"
        if args.scene:
            scene = scenes.validate(_json_arg(args.scene))
            scene_file = directory / f"{source.stem}-scene.json"
            scene_file.write_text(json.dumps(scene))
            extra += ["--scene", str(scene_file)]
            tag = f"r{scene.get('revision') or 0}"
        if args.view:
            camera = _json_arg(args.view)
            extra += ["--view", json.dumps(camera.get("structure_studio_view", camera))]
        if args.fit:
            extra.append("--fit")
        session = run_pymol_session([str(source), "-o", str(unique(directory / f"{source.stem}-{tag}.pse")), *extra])
        source = Path(session["session"])
    label = Path(args.style_json).stem if args.style_json else args.preset
    output = unique(Path(args.output).expanduser() if args.output else directory / f"{source.stem}-{label}.png")
    command = [str(executable), "render", str(source), "-o", str(output), "--timeout", str(args.timeout)]
    command += ["--style-json", args.style_json] if args.style_json else ["--preset", args.preset]
    adjusted = cpu_defaults(args)
    for key in ("width", "height", "samples", "duration", "fps", "rock_angle"):
        value = getattr(args, key) if getattr(args, key) is not None else adjusted.get(key)
        if value is not None:
            command += [f"--{key.replace('_', '-')}", str(value)]
    for flag in ("rough", "save_blend", "gif", "transparent"):
        if getattr(args, flag):
            command.append(f"--{flag.replace('_', '-')}")
    if args.no_preserve_view:
        command.append("--no-preserve-view")
    if args.animation:
        command += ["--animation", args.animation]
    print(f"[structure-studio] Rendering {source.name} with {label}…", file=sys.stderr, flush=True)
    started = time.time()
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=args.timeout + 120)
    if result.returncode:
        raise RuntimeError("Render failed:\n" + (result.stderr or result.stdout)[-4000:])
    payload = json.loads(result.stdout)
    payload.update(preset=label, seconds=round(time.time() - started, 1), cpu_adjustments=adjusted,
                   input=str(source))
    if session:
        payload["outputs"]["session.pse"] = session["session"]
    if args.workspace:
        payload.update(_workspace_steps(args, payload, output))
    result_file = artifact_files.write_json(payload, str(output.with_suffix(".result.json")))
    payload["result_file"] = result_file
    return payload


def _workspace_steps(args, payload: dict, output: Path) -> dict:
    """Bundle the session files and spell out the remaining tool calls for the workspace."""
    files = {}
    if payload["outputs"].get("session.pse"):
        files["pse"] = payload["outputs"]["session.pse"]
    if payload["outputs"].get("render.blend"):
        blend = output.with_suffix(".blend")
        blend.write_bytes(Path(payload["outputs"]["render.blend"]).read_bytes())
        files["blend"] = str(blend)
    session = artifact_files.bundle(files, str(output.with_suffix(""))) if files else None
    url = args.url or "<workspace url>"
    job = args.job or "<render id>"
    result_file = str(output.with_suffix(".result.json"))
    record_file = str(output.with_suffix(".record.json"))
    session_ids = " ".join(f"<id of {Path(p).name}>" for p in (session or {}).get("parts", []))
    steps = [f"Artifact publish url={url} asset=true file_paths=[\"{payload['outputs']['render.png']}\"]"]
    steps += [f"Artifact publish url={url} asset=true file_path=\"{part}\"" for part in (session or {}).get("parts", [])]
    steps.append(f"python3 {Path(__file__).resolve()} record {result_file} --image <id of the PNG>"
                 + (f" --session {session_ids}" if session else "")
                 + (f" --label {shlex.quote(args.label)}" if args.label else "") + f" -o {record_file}")
    steps.append(f"ArtifactData set url={url} collection=renders doc_id={job} file_path={record_file} if_version=<placeholder version>")
    return {"session": session, "workspace_snapshot": args.workspace, "label": args.label, "note": args.note,
            "scene_file": args.scene, "camera_file": args.view if args.view and Path(args.view).is_file() else "",
            "next": steps}


def import_session(path: Path, directory: Path) -> dict:
    """Run pse_import.py in the PyMOL environment: mmCIF of the enabled objects, view and chain colors."""
    python = studio_env.pymol_python()
    result = subprocess.run([str(python), str(HERE / "pse_import.py"), str(path), str(directory)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Could not read the PyMOL session:\n" + (result.stderr or result.stdout)[-3000:])
    return json.loads(result.stdout)


def cmd_workspace(args):
    """Everything needed to publish a new workspace artifact for one structure (or PyMOL session)."""
    import viewer
    source = Path(args.structure).expanduser()
    session_name = source.name[:-4] if source.name.lower().endswith(".pse") else ""
    directory = Path(args.out_dir).expanduser() if args.out_dir else output_dir() / f"{(session_name or source.name.split('.')[0])}-workspace"
    directory.mkdir(parents=True, exist_ok=True)
    imported, camera, source_session = None, None, None
    structure_file = str(source)
    if session_name:
        imported = import_session(source, directory)
        structure_file, camera = imported["structure"], imported["camera"]
        source_session = artifact_files.bundle({"pse": str(source)}, str(directory / f"{session_name}-uploaded"))
    data, name = structure_io.read_structure_bytes(structure_file)
    summary = structure_io.summarize(data, name)
    if summary["format"] == "bcif":
        raise ValueError("Workspaces need mmCIF or PDB text; convert BinaryCIF first")
    if session_name and not any(c.get("gene") for c in summary["chains"]):
        summary["suggested_workspace_title"] = session_name
    title = args.title or summary["suggested_workspace_title"]
    start = viewer.initial_scene(summary, style=args.style, color=args.color, ligand=args.ligand)
    if imported:
        # Keep the session's own chain colors so the panel and renders look like the user's PyMOL view.
        start["chain_colors"].update({c: v.lower() for c, v in imported["chain_colors"].items() if c in start["chain_colors"]})
        start["note"] = "imported from " + source.name
    page = viewer.build(structure_file, str(directory / "page.html"), title=title, entry_id=args.id, source=args.source,
                        scene=start, camera=camera, workspace=True)
    structure_text = directory / (name + ".txt")
    structure_text.write_bytes(data)
    meta = {"schema": 1, "title": title, "entry_id": summary["id"], "source": args.source,
            "description": summary["title"], "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "structure": {"asset_id": None, "filename": name, "format": summary["format"], "bytes": len(data),
                          "sha256": artifact_files.sha256(data), "encoding": "text"},
            "summary": {k: summary[k] for k in ("method", "resolution_angstrom", "atoms", "chains", "ions", "waters",
                                                "likely_predicted_model")},
            "ligands": [{"key": l["key"], "name": l["name"], "covalent": l.get("covalent", False), "additive": l["additive"]}
                        for l in summary["ligands"]]}
    if source_session:
        meta["source_session"] = {"format": source_session["format"], "asset_ids": None, "files": source_session["files"],
                                  "objects": imported["objects"], "chain_mapping": imported["chain_mapping"]}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    scene = dict(page["scene"], written_at=now)
    genes = sorted({c["gene"] for c in summary["chains"] if c.get("gene")})
    extra = {}
    if camera:
        extra["camera_file"] = artifact_files.write_json(dict(camera, by="claude", request_id="initial", saved_at=now),
                                                         str(directory / "camera.json"))
    if source_session:
        extra["session_uploads"] = source_session["parts"]
    return {**extra, "page": page["viewer"], "title": title, "bytes": page["bytes"],
            "description": f"Structure Studio workspace for {summary['id']}"
                           + (f" ({', '.join(genes)})" if genes else "") + f": {summary['title'][:120]}",
            "capabilities": viewer.WORKSPACE_CAPABILITIES, "icon": "molecule",
            "structure_upload": str(structure_text),
            "meta_file": artifact_files.write_json(meta, str(directory / "meta.json")),
            "scene_file": artifact_files.write_json(scene, str(directory / "scene.json")),
            "next": ["Publish `page` with `capabilities`, `icon` and `description`.",
                     "Upload `structure_upload` as an asset of the new artifact (asset: true)"
                     + (", and each file in `session_uploads` (one call each)." if source_session else "."),
                     "Run `studio.py meta META_FILE --structure-asset ID`" + (" --source-session IDS…" if source_session else "") + ".",
                     "Batch-write workspace/meta from `meta_file` and scene/current from `scene_file`"
                     + (", and scene/camera from `camera_file`." if camera else ".")]}


def cmd_meta(args):
    path = Path(args.meta).expanduser()
    meta = json.loads(path.read_text())
    meta["structure"]["asset_id"] = args.structure_asset
    if meta.get("source_session"):
        if not args.source_session:
            raise ValueError("This workspace came from a .pse: also pass --source-session with the uploaded bundle id(s)")
        meta["source_session"]["asset_ids"] = args.source_session
    path.write_text(json.dumps(meta, indent=2))
    return {"meta_file": str(path.resolve()), "structure": meta["structure"]}


def _shortcuts(args, current: dict) -> dict:
    """Turn the common edits into a patch, so callers never rebuild lists by hand."""
    patch = scenes.load(args.patch) if args.patch else {}
    hidden = list(patch.get("hidden", current.get("hidden", [])))
    layers = list(patch.get("layers", current.get("layers", [])))
    only_chain = lambda h: isinstance(h, dict) and set(h) == {"chain"}
    chains_of = lambda h: [h["chain"]] if isinstance(h["chain"], str) else list(h["chain"])
    for chain in args.show_chain:
        hidden = [h for h in hidden if not (only_chain(h) and chain in chains_of(h))]
    for chain in args.hide_chain:
        if not any(only_chain(h) and chain in chains_of(h) for h in hidden):
            hidden.append({"chain": chain})
    if args.clear_site or args.site:
        layers = [l for l in layers if not l.get("select", {}).get("site")]
    if args.site:
        layers.append({"select": {"site": args.site}, "representation": "sticks", "color": "element",
                       **({"label": True} if args.label_site else {})})
        patch.update(focus={"ligand": args.site}, opacity=patch.get("opacity", 0.45))
    elif args.clear_site:
        patch.update(focus=None, opacity=patch.get("opacity", 1.0))
    if args.show_chain or args.hide_chain or "hidden" in patch:
        patch["hidden"] = hidden
    if args.site or args.clear_site or "layers" in patch:
        patch["layers"] = layers
    if args.style:
        patch["representation"] = args.style
    if args.color:
        patch["color"] = args.color
    if args.reset_view:
        patch = dict(scenes.default_scene(chain_colors=current.get("chain_colors", {})), **patch)
    return patch


def cmd_scene(args):
    """Validate a scene, optionally applying edits; writes the next revision for scene/current."""
    current = scenes.load(args.current) if args.current else scenes.default_scene()
    summary = None
    if args.structure:
        summary = structure_io.summarize(*structure_io.read_structure_bytes(args.structure))
    patch = _shortcuts(args, current)
    if patch:
        current = scenes.merge(current, patch)
        current.update(updated_by="claude", note=args.note or "", written_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    result = scenes.validate(current, summary)
    out = {"scene": result}
    if args.output:
        out["scene_file"] = artifact_files.write_json(result, args.output)
    return out


def cmd_restore(args):
    """Rebuild a render's .pse/.blend from its downloaded session bundle file(s)."""
    files = [Path(p).expanduser() for p in args.files]
    if args.record:
        record = scenes.load(args.record)
        ids = (record.get("session") or {}).get("asset_ids", [])
        if not ids:
            raise ValueError("This render record has no session bundle (older records store `pse`/`blend` parts: use `unpack`)")
        roots = [Path(args.downloads).expanduser()] if args.downloads else [Path.cwd(), Path("/tmp")]
        for asset in ids:
            match = next((f for root in roots for f in root.rglob(f"{asset}*") if f.is_file()), None)
            if not match:
                raise ValueError(f"Bundle asset {asset} not downloaded yet: Artifact read url=<workspace> path={asset}")
            files.append(match)
    if not files:
        raise ValueError("Give bundle files, or --record with --downloads")
    out = args.out_dir or str(output_dir())
    return {"restored": artifact_files.restore([str(f) for f in files], out)}


def cmd_unpack(args):
    return artifact_files.unpack(args.parts, args.output, args.sha256)


def cmd_record(args):
    render = json.loads(Path(args.render).expanduser().read_text())
    assets = {"image": args.image, "session": args.session}
    scene = _json_arg(args.scene or render.get("scene_file", ""))
    camera = _json_arg(args.camera or render.get("camera_file", ""))
    if camera:
        camera = camera.get("structure_studio_view", camera)
        camera = {k: camera[k] for k in ("position", "target", "up", "fov_deg", "mode") if k in camera}
    doc = artifact_files.record(render, assets, label=args.label or render.get("label") or "", note=args.note or render.get("note") or "",
                                scene=scene, camera=camera)
    output = args.output or str(Path(render["outputs"]["render.png"]).with_suffix(".record.json"))
    return {"record_file": artifact_files.write_json(doc, output), "record": doc}


def cmd_viewer(args):
    import viewer
    camera = None
    if args.view:
        from camera import load_view
        camera = load_view(args.view)
    elif args.camera_from_session:
        camera = json.loads(Path(args.camera_from_session).read_text()).get("molstar_camera")
    directory = output_dir(args.out_dir)
    stem = Path(args.structure).name.split(".")[0]
    output = Path(args.output).expanduser() if args.output else directory / f"{stem}-viewer.html"
    predicted = True if args.predicted else (False if args.experimental else None)
    return viewer.build(args.structure, str(output), title=args.title, entry_id=args.id, source=args.source,
                        renders=args.render, files=args.file, style=args.style, color=args.color, ligand=args.ligand,
                        water=args.water, camera=camera, predicted=predicted, tab=args.tab, standalone=args.standalone)


def parser():
    p = argparse.ArgumentParser(prog="studio.py", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", help="Install PyMOL and Blender (bpy) into the studio home")
    s.add_argument("--only", choices=("all", "pymol", "render"), default="all")
    s.add_argument("--background", action="store_true", help="Install in the background and return at once; later commands wait for it")
    s.add_argument("--use-existing", help="Use an existing blender-render executable instead of installing")
    s.add_argument("--blender-render-home", help="BLENDER_RENDER_HOME for --use-existing")
    sub.add_parser("doctor", help="Report what is installed")

    s = sub.add_parser("fetch", help="Download a PDB / AlphaFold DB entry, or validate a local file")
    s.add_argument("identifier")
    s.add_argument("--dir", default="")

    s = sub.add_parser("info", help="Summarize chains, ligands and metadata")
    s.add_argument("structure")

    s = sub.add_parser("presets", help="List Blender presets")
    s.add_argument("--general", action="store_true", help="Only presets that work on any structure")

    s = sub.add_parser("session", help="Write a PyMOL .pse (extra flags pass to pymol_session.py)")
    s.add_argument("structure")
    s.add_argument("-o", "--output", default="")
    s.add_argument("--style", default="overview", choices=("overview", "surface", "site", "secondary", "confidence"))
    s.add_argument("--png", action="store_true", help="Also ray-trace NAME.png next to the session")
    s.add_argument("--out-dir", default="")

    s = sub.add_parser("render", help="Render with Blender + Molecular Nodes using a preset")
    s.add_argument("structure", help=".cif/.pdb (optionally .gz) or a .pse session")
    s.add_argument("--preset", default="mint_sky_assembly")
    s.add_argument("--style-json", default="")
    s.add_argument("-o", "--output", default="")
    s.add_argument("--out-dir", default="")
    s.add_argument("--view", "--camera", dest="view", default="", help="Camera JSON (scene/camera or Copy view), inline or a file")
    s.add_argument("--scene", default="", help="Shared scene JSON (scene/current), inline or a file: renders what the panel shows")
    s.add_argument("--workspace", default="", help="Folder from `ArtifactData list scene out_dir=…`: renders the panel's scene and camera, keeps .pse/.blend, prints the next calls")
    s.add_argument("--url", default="", help="Workspace URL, filled into the printed next calls")
    s.add_argument("--job", default="", help="renders/<id> of the placeholder posted before rendering")
    s.add_argument("--label", default="", help="Caption for the render card")
    s.add_argument("--note", default="", help="One-line description for the render card")
    s.add_argument("--fit", action="store_true", help="Keep the camera's angle but zoom out so everything visible is in frame")
    s.add_argument("--rough", action="store_true", help="Fast preview settings")
    s.add_argument("--full-quality", action="store_true", help="Do not cap size/samples on CPU")
    s.add_argument("--width", type=int)
    s.add_argument("--height", type=int)
    s.add_argument("--samples", type=int)
    s.add_argument("--save-blend", action="store_true")
    s.add_argument("--transparent", action="store_true")
    s.add_argument("--no-preserve-view", action="store_true", help="Use the preset's authored camera")
    s.add_argument("--animation", choices=("rock", "360"))
    s.add_argument("--gif", action="store_true")
    s.add_argument("--duration", type=float)
    s.add_argument("--fps", type=int)
    s.add_argument("--rock-angle", type=float)
    s.add_argument("--timeout", type=float, default=900)

    s = sub.add_parser("workspace", help="Prepare a live workspace artifact for one structure or PyMOL session")
    s.add_argument("structure", help=".cif/.pdb (optionally .gz) or a .pse session (needs `setup --only pymol`)")
    s.add_argument("--title", default="", help="Defaults to genes and ID, e.g. 'UBE2N–UBE2V2 · 9LHJ'")
    s.add_argument("--id", default="")
    s.add_argument("--source", default="")
    s.add_argument("--style", default="cartoon", choices=("cartoon", "surface", "atoms", "site"))
    s.add_argument("--color", default="chain", choices=("chain", "ss", "rainbow", "bfactor", "plddt"))
    s.add_argument("--ligand", default="")
    s.add_argument("--out-dir", default="")

    s = sub.add_parser("meta", help="Record the uploaded structure's asset id in meta.json")
    s.add_argument("meta")
    s.add_argument("--structure-asset", required=True)
    s.add_argument("--source-session", nargs="*", default=[], help="Asset id(s) of the uploaded .pse bundle, for .pse workspaces")

    s = sub.add_parser("scene", help="Validate a scene, or apply a patch to it for the next revision")
    s.add_argument("current", nargs="?", default="", help="Current scene/current JSON (inline or a file)")
    s.add_argument("--patch", default="", help="Fields to change, e.g. '{\"hidden\": [{\"chain\": \"A\"}]}'")
    s.add_argument("--hide-chain", action="append", default=[], help="Hide a chain (repeatable)")
    s.add_argument("--show-chain", action="append", default=[], help="Show a hidden chain again (repeatable)")
    s.add_argument("--site", default="", help="Show and focus a ligand's binding site, e.g. 'A1EJS B74' or 'HEM'")
    s.add_argument("--label-site", action="store_true", help="With --site: label the site residues")
    s.add_argument("--clear-site", action="store_true", help="Remove binding-site layers and focus")
    s.add_argument("--style", choices=scenes.REPRESENTATIONS, help="Polymer representation")
    s.add_argument("--color", help="chain, ss, rainbow, bfactor, plddt, element or #rrggbb")
    s.add_argument("--reset-view", action="store_true", help="Back to the default scene (keeps chain colors)")
    s.add_argument("--note", default="", help="Short description shown in the panel, e.g. 'hide chain A'")
    s.add_argument("--structure", default="", help="Check chains and ligands against this file")
    s.add_argument("-o", "--output", default="")

    s = sub.add_parser("restore", help="Rebuild a render's .pse/.blend from its downloaded session bundle")
    s.add_argument("files", nargs="*", help="Downloaded bundle file(s)")
    s.add_argument("--record", default="", help="The render's record (file or JSON); finds its bundle among downloads")
    s.add_argument("--downloads", default="", help="Folder where `Artifact read` saved the bundle")
    s.add_argument("--out-dir", default="")

    s = sub.add_parser("unpack", help="Rebuild a file from older records' text parts (in order)")
    s.add_argument("parts", nargs="+")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--sha256", default="", help="Expected checksum from the render record")

    s = sub.add_parser("record", help="Write the renders/<id> document after uploading a render")
    s.add_argument("render", help="JSON saved from `studio.py render --pack`")
    s.add_argument("--image", required=True, help="Asset id of the uploaded PNG")
    s.add_argument("--session", nargs="*", default=[], help="Asset ids of the uploaded session bundle file(s), in order")
    s.add_argument("--label", default="")
    s.add_argument("--note", default="")
    s.add_argument("--scene", default="")
    s.add_argument("--camera", default="")
    s.add_argument("-o", "--output", default="")

    s = sub.add_parser("viewer", help="Build an offline Mol* page (a workspace is the default deliverable)")
    s.add_argument("structure")
    s.add_argument("-o", "--output", default="")
    s.add_argument("--out-dir", default="")
    s.add_argument("--render", action="append", default=[], help="PNG to show in the Renders tab: PATH or PATH=Label|description")
    s.add_argument("--file", action="append", default=[], help="File to offer as a .zip download (.pse, .blend, .cif …)")
    s.add_argument("--style", default="cartoon", choices=("cartoon", "surface", "atoms", "site"))
    s.add_argument("--color", default="chain", choices=("chain", "ss", "rainbow", "bfactor", "plddt"))
    s.add_argument("--ligand", default="")
    s.add_argument("--water", action="store_true")
    s.add_argument("--view", default="", help="Initial camera (Copy view JSON)")
    s.add_argument("--camera-from-session", default="", help="JSON printed by `session`, to start at its pose")
    s.add_argument("--title", default="")
    s.add_argument("--id", default="")
    s.add_argument("--source", default="")
    s.add_argument("--tab", default="", choices=("", "3d", "renders", "files"))
    s.add_argument("--predicted", action="store_true")
    s.add_argument("--experimental", action="store_true")
    s.add_argument("--standalone", action="store_true", help="Full HTML document for opening locally")
    return p


def main(argv=None):
    args, extra = parser().parse_known_args(argv)
    if extra and args.command != "session":
        parser().error("unrecognized arguments: " + " ".join(extra))
    handlers = {"setup": cmd_setup, "doctor": lambda a: studio_env.doctor(), "fetch": cmd_fetch, "info": cmd_info,
                "presets": cmd_presets, "render": cmd_render, "viewer": cmd_viewer, "workspace": cmd_workspace,
                "meta": cmd_meta, "scene": cmd_scene, "restore": cmd_restore, "unpack": cmd_unpack, "record": cmd_record,
                "session": lambda a: cmd_session(a, extra)}
    try:
        result = handlers[args.command](args)
    except (studio_env.SetupError, scenes.SceneError, RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if not (isinstance(result, dict) and result.get("error")) else 2


if __name__ == "__main__":
    sys.exit(main())
