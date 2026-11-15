"""Build a PyMOL session (.pse) and an optional ray-traced PNG in a named figure style.

Runs inside the PyMOL environment (python with pymol2); prints a JSON summary.
The saved pose, colors and enabled objects carry over to Blender renders of the session.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import pymol2

from camera import load_view, molstar_to_pymol, pymol_to_molstar
import scene as scenes

from molecular_data import PALETTE, LIGAND_CARBON, HIGHLIGHT_CARBON, SS_COLORS, PLDDT, ADDITIVES
# One ligand classification for Studio and co-pymol (re-exported for animation_session).
from co_pymol.scene.ligands import ligand_records, residue_selection  # noqa: F401

STYLES = ("overview", "surface", "site", "secondary", "confidence")


def rgb(hex_color):
    return [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", help="Structure file (.cif/.pdb, optionally .gz) or an existing .pse")
    p.add_argument("-o", "--output", required=True, help="Session path to write (.pse)")
    p.add_argument("--style", choices=STYLES, help="Figure style; existing .pse scenes are preserved when omitted")
    p.add_argument("--ligand", default="", help="Ligand key such as 'HEM A142' or a residue name such as 'HEM'")
    p.add_argument("--chains", default="", help="Keep only these chains, e.g. A,B")
    p.add_argument("--keep-waters", action="store_true")
    p.add_argument("--show-additives", action="store_true", help="Show crystallization additives (GOL, SO4, …)")
    p.add_argument("--highlight", action="append", default=[], help="PyMOL selection to show as magenta sticks (repeatable)")
    p.add_argument("--cutoff", type=float, default=5.0, help="Binding-site distance in Å")
    p.add_argument("--no-labels", action="store_true", help="Skip residue labels in the binding-site style")
    p.add_argument("--no-contacts", action="store_true", help="Skip polar-contact dashes in the binding-site style")
    p.add_argument("--view", "--camera", dest="view", default="", help="Viewer camera JSON (string or file path)")
    p.add_argument("--fit", action="store_true", help="Keep the view's angle but zoom to fit everything visible")
    p.add_argument("--scene", default="", help="Shared scene JSON (string or file); replaces --style, --chains and --highlight")
    p.add_argument("--background", default="white", help="white, black, or a #rrggbb color")
    p.add_argument("--png", default="", help="Also ray-trace a PNG here")
    p.add_argument("--bundle", default="", help="Also write the renderer's scene bundle (JSON) of the saved session here")
    p.add_argument("--width", type=int, default=1600)
    p.add_argument("--height", type=int, default=1200)
    return p.parse_args(argv)


def color_hetero(cmd, selection):
    """Element colors for non-carbon atoms, keeping the carbon color (util.cnc without the global cmd)."""
    cmd.color("atomic", f"({selection}) and not elem C")


def open_slab(cmd, obj, margin=5.0):
    """Move the clip planes outside the whole object. Blender copies PyMOL's slab into the
    camera's clip range, so a tight zoom slab would slice the cartoon in renders."""
    view = list(cmd.get_view())
    (x0, y0, z0), (x1, y1, z1) = cmd.get_extent(obj)
    center = [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2]
    radius = 0.5 * ((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2) ** 0.5 + margin
    offset = sum((center[i] - view[12 + i]) ** 2 for i in range(3)) ** 0.5
    distance = -view[11]
    view[15] = max(1.0, distance - radius - offset)
    view[16] = distance + radius + offset
    cmd.set_view(view)


def show_ligands(cmd, obj, ligands):
    if not ligands:
        return
    selection = " or ".join(residue_selection(obj, ligand) for ligand in ligands)
    cmd.set_color("studio_ligand", rgb(LIGAND_CARBON))
    cmd.show("sticks", selection)
    cmd.color("studio_ligand", f"({selection}) and elem C")
    color_hetero(cmd, selection)


def show_ions(cmd, selection):
    cmd.show("spheres", selection)
    cmd.set("sphere_scale", 0.6, selection)


def build(args):
    source = Path(args.input).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    preserve = source.suffix.lower() == ".pse" and not args.style and not args.scene
    result = {"session": str(output), "style": "preserved" if preserve else args.style or "overview"}
    with pymol2.PyMOL() as instance:
        cmd = instance.cmd
        name = re.sub(r"[^A-Za-z0-9_]", "_", source.name.split(".")[0]) or "structure"
        if name[0].isdigit():
            name = "s" + name
        cmd.load(str(source), name if source.suffix.lower() != ".pse" else None)
        obj = "(enabled)" if source.suffix.lower() == ".pse" else name
        if not cmd.count_atoms(obj):
            raise ValueError("The input has no enabled atoms to render")
        if preserve:
            if args.chains or args.highlight or args.ligand:
                raise ValueError("Choose --style or --scene to restyle an existing session")
            finish_view(cmd, obj, args, source, result)
            return save(cmd, obj, args, output, result)
        args.style = args.style or "overview"
        scene = scenes.validate(scenes.load(args.scene)) if args.scene else None
        if args.chains:
            cmd.remove(f"{obj} and not ({scenes.chain_selection([c.strip() for c in args.chains.split(',')])})")
        if not args.keep_waters and not (scene and scene["waters"]):
            cmd.remove(f"{obj} and solvent")
        cmd.remove(f"{obj} and not alt ''+A")
        cmd.alter(obj, "alt=''")
        cmd.remove(f"{obj} and hydro")
        cmd.dss(obj)

        cmd.hide("everything", obj)
        cmd.set_color("studio_highlight", rgb(HIGHLIGHT_CARBON))
        background = {"white": "#ffffff", "black": "#000000"}.get(args.background, args.background)
        cmd.set_color("studio_bg", rgb(background))
        cmd.bg_color("studio_bg")
        for setting, value in (("ray_opaque_background", 1), ("antialias", 2), ("ambient", 0.35), ("specular", 0.25),
                               ("ray_shadows", 0), ("depth_cue", 0), ("cartoon_side_chain_helper", 1),
                               ("stick_radius", 0.2), ("cartoon_transparency", 0), ("orthoscopic", 0),
                               ("label_size", 16), ("label_font_id", 7), ("float_labels", 1),
                               ("dash_gap", 0.25), ("dash_radius", 0.06), ("surface_quality", 1)):
            cmd.set(setting, value)

        if scene:
            result.update(apply_scene(cmd, obj, scene, background))
        else:
            result.update(apply_style(cmd, obj, args, background))
        finish_view(cmd, obj, args, source, result, focus=result.pop("_focus", None))
        return save(cmd, obj, args, output, result)


def apply_style(cmd, obj, args, background):
    """Apply a named figure style, including its binding-site and highlight options."""
    result = {}
    chains = cmd.get_chains(obj)
    chain_colors = {chain: PALETTE[i % len(PALETTE)] for i, chain in enumerate(chains)}
    color_theme(cmd, "chain", obj, chain_colors)
    result["chains"] = chain_colors

    ligands = ligand_records(cmd, obj)
    visible = [l for l in ligands if args.show_additives or not l["additive"]]
    result["ligands"] = [l["key"] for l in ligands]
    primary = None
    if args.ligand:
        wanted = args.ligand.strip().upper()
        primary = next((l for l in ligands if l["key"].upper() == wanted), None) or \
            next((l for l in ligands if l["resn"].upper() == wanted), None)
        if primary is None:
            raise SystemExit(f"Ligand {args.ligand!r} not found. Available: {', '.join(result['ligands']) or 'none'}")
        if primary not in visible:
            visible.append(primary)
    elif ligands:
        primary = next((l for l in ligands if not l["additive"]), ligands[0])

    cmd.show("cartoon", f"{obj} and polymer")
    show_ligands(cmd, obj, visible)
    show_ions(cmd, f"{obj} and inorganic and not resn {'+'.join(ADDITIVES)}")

    if args.style == "surface":
        cmd.show("surface", f"{obj} and polymer")
        cmd.set("transparency", 0.1)
    elif args.style == "secondary":
        color_theme(cmd, "ss", f"{obj} and polymer", chain_colors)
    elif args.style == "confidence":
        color_theme(cmd, "plddt", f"{obj} and polymer", chain_colors)
        result["legend"] = {label: color for _, color, label in PLDDT}
    elif args.style == "site":
        if primary is None:
            raise SystemExit("The binding-site style needs a ligand, but this structure has none.")
        ligand_sel = residue_selection(obj, primary)
        site = f"byres ({obj} and polymer within {args.cutoff} of {ligand_sel}) and not {ligand_sel}"
        cmd.select("site_residues", site)
        cmd.show("sticks", "site_residues and (sidechain or name CA)")
        color_hetero(cmd, "site_residues")
        cmd.set("cartoon_transparency", 0.55, obj)
        if not args.no_contacts:
            cmd.distance("polar_contacts", ligand_sel, "site_residues", mode=2)
            cmd.hide("labels", "polar_contacts")
            cmd.color("yellow", "polar_contacts")
            pairs = cmd.find_pairs(ligand_sel, "site_residues", mode=1, cutoff=3.6)
            result["polar_contacts"] = len(pairs)
        if not args.no_labels:
            # Label the residues that matter most: polar partners and the closest shell, at most 12.
            contacts = cmd.find_pairs(ligand_sel, "site_residues", mode=1, cutoff=3.6)
            partners = " or ".join(f"(byres index {index} and model {model})" for _, (model, index) in contacts)
            key = f"byres (site_residues within 3.8 of {ligand_sel})" + (f" or {partners}" if partners else "")
            cmd.select("site_labelled", f"site_residues and ({key}) and name CA")
            chosen = []
            cmd.iterate("site_labelled", "chosen.append((chain, resi))", space={"chosen": chosen})
            for chain, resi in chosen[:12]:
                cmd.label(f"site_labelled and {scenes.chain_selection(chain)} and resi \\{resi}", "f'{resn[0]}{resn[1:].lower()}{resi}'")
            cmd.set("label_color", "grey20" if background == "#ffffff" else "grey90")
            cmd.delete("site_labelled")
        residues = {"items": []}
        cmd.iterate("site_residues and name CA", "items.append(f'{resn} {chain}{resi}')", space=residues)
        result["site_residues"] = residues["items"]
        cmd.deselect()
    result["ligand"] = primary["key"] if primary else None

    for i, selection in enumerate(args.highlight):
        label = f"highlight_{i + 1}"
        cmd.select(label, f"{obj} and ({selection})")
        cmd.show("sticks", f"{label} and (sidechain or name CA)")
        cmd.color("studio_highlight", f"{label} and elem C")
        color_hetero(cmd, label)
        result.setdefault("highlights", {})[label] = cmd.count_atoms(label)
    cmd.deselect()

    focus = f"{residue_selection(obj, primary)} or site_residues" if args.style == "site" and primary else None
    result["_focus"] = focus
    return result


def finish_view(cmd, obj, args, source, result, focus=None):
    if args.view:
        camera = load_view(args.view)
        (x0, y0, z0), (x1, y1, z1) = cmd.get_extent(obj)
        radius = 0.5 * ((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2) ** 0.5 + 10
        cmd.set_view(molstar_to_pymol(camera, radius))
        result["view_source"] = "viewer camera"
    elif focus and cmd.count_atoms(focus):
        cmd.orient(focus)
        cmd.zoom(focus, buffer=3, complete=1)
        result["view_source"] = "oriented on focus"
    elif source.suffix.lower() != ".pse":
        cmd.orient(obj)
        cmd.zoom(obj, buffer=2, complete=1)
        result["view_source"] = "oriented on structure"
    if getattr(args, "fit", False):
        cmd.zoom(obj, buffer=2, complete=1)  # keeps the rotation, recenters and refits
        result["view_source"] = result.get("view_source", "session") + ", fitted"
    if "view_source" in result:
        open_slab(cmd, obj)


def save(cmd, obj, args, output, result):
    view = list(cmd.get_view())
    result["pymol_view"] = view
    result["molstar_camera"] = pymol_to_molstar(view)
    cmd.save(str(output))
    if args.bundle:
        result["bundle"] = write_bundle(cmd, output, Path(args.bundle).expanduser().resolve())
    if args.png:
        png = Path(args.png).expanduser().resolve()
        png.parent.mkdir(parents=True, exist_ok=True)
        cmd.png(str(png), width=args.width, height=args.height, dpi=300, ray=1)
        result["png"] = str(png)
    result["atoms"] = cmd.count_atoms(obj)
    result["object"] = obj
    return result


def write_bundle(cmd, session: Path, bundle: Path) -> str:
    """Export the session just saved with co-pymol's capture, so the renderer never reloads it.

    The payload matches what `blender-render render SESSION` would export from the
    saved file, including its provenance, without starting a second PyMOL.
    """
    import hashlib

    import studio_env
    studio_env.use_bundled_helpers()
    from co_pymol.scene.capture import capture_scene

    payload = capture_scene(cmd, "all", -1)  # co-pymol's default selection: identical bundles.
    payload.setdefault("provenance", {}).update(
        filename=session.name, sha256=hashlib.sha256(session.read_bytes()).hexdigest(),
        format=".pse", pymol_version=list(cmd.get_version())[:2])
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(json.dumps(payload, allow_nan=False))
    return str(bundle)


def color_theme(cmd, theme, selection, chain_colors):
    """Apply a theme; callers can then overlay element colors with color_hetero."""
    if theme.startswith("#"):
        name = "studio_" + theme[1:].lower()
        cmd.set_color(name, rgb(theme))
        cmd.color(name, selection)
    elif theme in {"chain", "element"}:
        for chain, color in chain_colors.items():
            name = "studio_" + color[1:].lower()
            cmd.set_color(name, rgb(color))
            cmd.color(name, f"({selection}) and {scenes.chain_selection(chain)}")
    elif theme == "ss":
        for code, color in SS_COLORS.items():
            cmd.set_color(f"studio_ss_{code}", rgb(color))
            cmd.color(f"studio_ss_{code}", f"({selection}) and ss {code if code != 'L' else 'L+'}")
    elif theme == "rainbow":
        for chain in chain_colors:
            chain_atoms = f"({selection}) and {scenes.chain_selection(chain)} and polymer"
            if cmd.count_atoms(chain_atoms):
                cmd.spectrum("resi", "rainbow", chain_atoms)
    elif theme == "bfactor":
        cmd.spectrum("b", "blue_white_red", selection)
    elif theme == "plddt":
        for i, (_, color, _) in enumerate(PLDDT):
            cmd.set_color(f"studio_plddt{i}", rgb(color))
        for i, (high, low) in enumerate([(None, 90), (90, 70), (70, 50), (50, None)]):
            expr = " and ".join(([f"b < {high}"] if high else []) + ([f"b > {low}"] if low is not None else [])) or "all"
            cmd.color(f"studio_plddt{i}", f"({selection}) and ({expr})")


def apply_scene(cmd, obj, scene, background):
    """Reproduce the viewer's shared scene: hidden parts move to a disabled object, so Blender skips them."""
    out = {"style": "scene", "scene_revision": scene.get("revision")}
    # Chain colors come from the scene (written when the workspace is created) or, failing that,
    # from file order before anything is hidden, so hiding a chain never recolors the others.
    order = []
    cmd.iterate(f"{obj} and polymer and (name CA or name P)", "order.append(chain) if chain not in order else None", space={"order": order})
    chain_colors = {c: scene["chain_colors"].get(c, PALETTE[i % len(PALETTE)]) for i, c in enumerate(order)}
    hidden = [scenes.to_pymol(selection, obj) for selection in scene["hidden"]]
    for flag, kind in (("ligands", "ligand"), ("ions", "ion"), ("waters", "water")):
        if not scene[flag]:
            hidden.append(scenes.to_pymol({"kind": kind}, obj))
    if hidden:
        expression = " or ".join(f"({h})" for h in hidden)
        out["hidden_atoms"] = cmd.count_atoms(expression)
        if out["hidden_atoms"]:
            cmd.extract("studio_hidden", expression)
            cmd.disable("studio_hidden")
    out["chains"] = {c: v for c, v in chain_colors.items() if c in cmd.get_chains(obj)}
    polymer = f"{obj} and polymer"
    rep = {"cartoon": "cartoon", "surface": "surface", "spacefill": "spheres", "putty": "cartoon", "backbone": "ribbon"}
    if scene["representation"] != "hidden":
        cmd.show(rep[scene["representation"]], polymer)
        if scene["representation"] == "putty":
            cmd.cartoon("putty", polymer)
    color_theme(cmd, scene["color"], polymer, chain_colors)
    color_hetero(cmd, polymer)
    transparency = round(1 - scene["opacity"], 3)
    for setting in ("cartoon_transparency", "transparency", "sphere_transparency"):
        cmd.set(setting, transparency, obj)
    ligands = [l for l in ligand_records(cmd, obj) if not l["additive"]] if scene["ligands"] else []
    show_ligands(cmd, obj, ligands)
    out["ligands"] = [l["key"] for l in ligands]
    if scene["ions"]:
        show_ions(cmd, f"{obj} and inorganic")
    if scene["waters"]:
        cmd.show("nb_spheres", f"{obj} and solvent")
    layer_rep = {"sticks": "sticks", "spacefill": "spheres", "surface": "surface", "cartoon": "cartoon", "putty": "cartoon"}
    out["layers"] = []
    for i, layer in enumerate(scene["layers"]):
        name = f"layer_{i + 1}"
        cmd.select(name, scenes.to_pymol(layer["select"], obj))
        count = cmd.count_atoms(name)
        out["layers"].append({"name": name, "atoms": count})
        if not count:
            continue
        cmd.show(layer_rep[layer["representation"]], name)
        color_theme(cmd, layer["color"], name, chain_colors)
        color_hetero(cmd, name)
        if "opacity" in layer:
            for setting in ("stick_transparency", "transparency", "sphere_transparency", "cartoon_transparency"):
                cmd.set(setting, round(1 - layer["opacity"], 3), name)
        if layer.get("label") is True:
            labelled = []
            cmd.iterate(f"{name} and name CA", "labelled.append((chain, resi))", space={"labelled": labelled})
            for chain, resi in labelled[:40]:
                cmd.label(f"{name} and name CA and {scenes.chain_selection(chain)} and resi \\{resi}", "f'{resn[0]}{resn[1:].lower()}{resi}'")
        elif isinstance(layer.get("label"), str):
            cmd.label(f"first ({name})", repr(layer["label"]))
    cmd.set("label_color", "grey20" if background == "#ffffff" else "grey90")
    cmd.deselect()
    if scene["focus"]:
        out["_focus"] = scenes.to_pymol(scene["focus"], obj)
    return out


def main(argv=None):
    args = parse_args(argv)
    # PyMOL writes feedback straight to file descriptor 1; keep stdout for the JSON result.
    stdout = os.dup(1)
    os.dup2(2, 1)
    try:
        result = build(args)
    finally:
        sys.stdout.flush()
        os.dup2(stdout, 1)
        os.close(stdout)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    sys.exit(main())
