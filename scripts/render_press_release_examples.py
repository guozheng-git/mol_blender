"""Render five Isomorphic-style Molecular Nodes examples.

Usage:
  blender --background --python scripts/render_press_release_examples.py -- \
    /path/to/base_scene.blend /path/to/structure.pdb /path/to/out_dir
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

import molecularnodes as mn


@dataclass
class RenderPreset:
    name: str
    bg: tuple[float, float, float, float]
    camera_yaw_deg: float
    camera_pitch_deg: float
    camera_distance_scale: float
    key_energy: float
    fill_energy: float
    rim_energy: float
    exposure: float
    look: str


PRESETS: list[RenderPreset] = [
    RenderPreset(
        name="01_dual_surface_reveal",
        bg=(0.66, 0.73, 0.79, 1.0),
        camera_yaw_deg=-10.0,
        camera_pitch_deg=3.0,
        camera_distance_scale=1.0,
        key_energy=680.0,
        fill_energy=260.0,
        rim_energy=220.0,
        exposure=-0.56,
        look="High Contrast",
    ),
    RenderPreset(
        name="02_frosted_wire_surface",
        bg=(0.74, 0.83, 0.90, 1.0),
        camera_yaw_deg=12.0,
        camera_pitch_deg=7.0,
        camera_distance_scale=0.92,
        key_energy=640.0,
        fill_energy=300.0,
        rim_energy=280.0,
        exposure=-0.52,
        look="High Contrast",
    ),
    RenderPreset(
        name="03_black_stage_pastel_cartoon",
        bg=(0.01, 0.01, 0.01, 1.0),
        camera_yaw_deg=24.0,
        camera_pitch_deg=11.0,
        camera_distance_scale=1.12,
        key_energy=820.0,
        fill_energy=420.0,
        rim_energy=560.0,
        exposure=-0.18,
        look="High Contrast",
    ),
    RenderPreset(
        name="04_mint_cream_surface",
        bg=(0.82, 0.88, 0.76, 1.0),
        camera_yaw_deg=-16.0,
        camera_pitch_deg=-6.0,
        camera_distance_scale=0.88,
        key_energy=620.0,
        fill_energy=240.0,
        rim_energy=200.0,
        exposure=-0.75,
        look="High Contrast",
    ),
    RenderPreset(
        name="05_editorial_peach_cyan",
        bg=(0.84, 0.79, 0.79, 1.0),
        camera_yaw_deg=24.0,
        camera_pitch_deg=-2.0,
        camera_distance_scale=0.92,
        key_energy=680.0,
        fill_energy=260.0,
        rim_energy=220.0,
        exposure=-0.72,
        look="High Contrast",
    ),
]


def parse_args() -> tuple[Path, Path, Path]:
    if "--" not in sys.argv:
        raise RuntimeError(
            "Expected args after '--': <base_scene.blend> <structure.pdb|cif> <out_dir>"
        )
    tail = sys.argv[sys.argv.index("--") + 1 :]
    if len(tail) != 3:
        raise RuntimeError(
            f"Expected 3 args after '--', got {len(tail)}: {tail}"
        )
    scene_path = Path(tail[0]).expanduser().resolve()
    structure_path = Path(tail[1]).expanduser().resolve()
    out_dir = Path(tail[2]).expanduser().resolve()
    return scene_path, structure_path, out_dir


def set_world_color(scene: bpy.types.Scene, rgba: tuple[float, float, float, float]) -> None:
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = rgba
        bg.inputs[1].default_value = 1.0


def clear_mesh_objects() -> None:
    for obj in list(bpy.data.objects):
        if obj.type == "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)


def clear_lights() -> None:
    for obj in list(bpy.data.objects):
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj, do_unlink=True)


def point_at_target(obj: bpy.types.Object, target: Vector) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def add_area_light(
    scene: bpy.types.Scene,
    name: str,
    location: tuple[float, float, float],
    color: tuple[float, float, float],
    energy: float,
    size: float,
    target: Vector,
) -> None:
    light_data = bpy.data.lights.new(name=name, type="AREA")
    light_data.energy = energy
    light_data.color = color
    light_data.size = size
    light_obj = bpy.data.objects.new(name, light_data)
    scene.collection.objects.link(light_obj)
    light_obj.location = location
    point_at_target(light_obj, target)


def setup_three_point_lighting(scene: bpy.types.Scene, target: Vector, preset: RenderPreset) -> None:
    clear_lights()
    add_area_light(
        scene=scene,
        name="KeyLight",
        location=(3.2, -3.0, 2.6),
        color=(1.0, 0.97, 0.94),
        energy=preset.key_energy,
        size=2.2,
        target=target,
    )
    add_area_light(
        scene=scene,
        name="FillLight",
        location=(-2.8, -2.2, 1.8),
        color=(0.85, 0.95, 1.0),
        energy=preset.fill_energy,
        size=3.0,
        target=target,
    )
    add_area_light(
        scene=scene,
        name="RimLight",
        location=(0.0, 3.2, 2.8),
        color=(0.95, 0.95, 1.0),
        energy=preset.rim_energy,
        size=2.0,
        target=target,
    )


def _set_input(
    node: bpy.types.ShaderNodeBsdfPrincipled,
    names: tuple[str, ...],
    value,
) -> None:
    for name in names:
        sock = node.inputs.get(name)
        if sock is not None:
            sock.default_value = value
            return


def create_soft_material(
    name: str,
    base_color: tuple[float, float, float, float],
    roughness: float,
    transmission: float,
    coat: float,
    alpha: float,
    emission_strength: float = 0.0,
) -> bpy.types.Material:
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)

    out = tree.nodes.new("ShaderNodeOutputMaterial")
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    _set_input(bsdf, ("Base Color",), base_color)
    _set_input(bsdf, ("Roughness",), roughness)
    _set_input(bsdf, ("Transmission Weight", "Transmission"), transmission)
    _set_input(bsdf, ("Coat Weight", "Coat"), coat)
    _set_input(bsdf, ("Alpha",), alpha)
    _set_input(bsdf, ("Emission Strength",), emission_strength)
    _set_input(bsdf, ("Specular IOR Level", "Specular"), 0.22)

    if hasattr(material, "blend_method"):
        material.blend_method = "HASHED" if alpha < 0.999 else "OPAQUE"
    if hasattr(material, "shadow_method"):
        material.shadow_method = "HASHED" if alpha < 0.999 else "OPAQUE"
    return material


def create_wire_overlay_material(
    name: str,
    fill_color: tuple[float, float, float, float],
    line_color: tuple[float, float, float, float],
    roughness: float,
    alpha: float,
) -> bpy.types.Material:
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)

    node_out = tree.nodes.new("ShaderNodeOutputMaterial")
    node_base = tree.nodes.new("ShaderNodeBsdfPrincipled")
    node_base.location = (-200, 80)
    _set_input(node_base, ("Base Color",), fill_color)
    _set_input(node_base, ("Roughness",), roughness)
    _set_input(node_base, ("Transmission Weight", "Transmission"), 0.25)
    _set_input(node_base, ("Coat Weight", "Coat"), 0.45)
    _set_input(node_base, ("Alpha",), alpha)

    node_wire = tree.nodes.new("ShaderNodeWireframe")
    node_wire.location = (-550, -180)
    if node_wire.inputs.get("Size") is not None:
        node_wire.inputs["Size"].default_value = 0.75
    node_wire.use_pixel_size = True

    node_line = tree.nodes.new("ShaderNodeBsdfPrincipled")
    node_line.location = (-200, -200)
    _set_input(node_line, ("Base Color",), line_color)
    _set_input(node_line, ("Roughness",), 0.24)

    node_mix = tree.nodes.new("ShaderNodeMixShader")
    node_mix.location = (120, -80)
    tree.links.new(node_wire.outputs["Fac"], node_mix.inputs["Fac"])
    tree.links.new(node_base.outputs["BSDF"], node_mix.inputs[1])
    tree.links.new(node_line.outputs["BSDF"], node_mix.inputs[2])
    tree.links.new(node_mix.outputs["Shader"], node_out.inputs["Surface"])

    if hasattr(material, "blend_method"):
        material.blend_method = "HASHED"
    if hasattr(material, "shadow_method"):
        material.shadow_method = "HASHED"
    return material


def get_residue_bounds(molecule) -> tuple[int, int, int]:
    res_ids = sorted({int(v) for v in molecule.array.res_id})
    if not res_ids:
        return 1, 1, 1
    lo = min(res_ids)
    hi = max(res_ids)
    mid = res_ids[len(res_ids) // 2]
    return lo, mid, hi


def get_object_center(obj: bpy.types.Object) -> Vector:
    bounds = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    center = Vector((0.0, 0.0, 0.0))
    for corner in bounds:
        center += corner
    return center / 8.0


def orbit_camera(
    scene: bpy.types.Scene,
    target: Vector,
    yaw_deg: float,
    pitch_deg: float,
    distance_scale: float,
) -> None:
    cam = scene.camera
    if cam is None:
        return
    vector = cam.location - target
    if vector.length < 1e-6:
        vector = Vector((0.0, -1.0, 0.4))

    yaw = Quaternion((0.0, 0.0, 1.0), math.radians(yaw_deg))
    pitch = Quaternion((1.0, 0.0, 0.0), math.radians(pitch_deg))
    vector = (yaw @ pitch) @ vector
    vector *= distance_scale
    cam.location = target + vector
    point_at_target(cam, target)


def add_ligand_style(molecule, material: bpy.types.Material, style_name: str = "sticks") -> None:
    selection = molecule.select.reset().is_ligand()
    molecule.add_style(style_name, selection=selection, material=material)


def apply_preset_styles(preset: RenderPreset, molecule) -> None:
    lo, mid, hi = get_residue_bounds(molecule)
    polymer_a = molecule.select.reset().res_id(range(lo, mid + 1)).not_solvent()
    polymer_b = molecule.select.reset().res_id(range(mid + 1, hi + 1)).not_solvent()
    polymer_all = molecule.select.reset().is_polymer()

    if preset.name.startswith("01_"):
        surf_a = create_soft_material("PR01_SurfaceA", (0.12, 0.44, 0.72, 1.0), 0.36, 0.01, 0.36, 0.50)
        surf_b = create_soft_material("PR01_SurfaceB", (0.18, 0.67, 0.54, 1.0), 0.38, 0.01, 0.34, 0.48)
        core = create_soft_material("PR01_Core", (0.90, 0.93, 0.96, 1.0), 0.36, 0.0, 0.10, 1.0)
        ligand = create_soft_material("PR01_Ligand", (0.98, 0.42, 0.67, 1.0), 0.22, 0.0, 0.14, 1.0)
        molecule.add_style("surface", selection=polymer_a, material=surf_a)
        molecule.add_style("surface", selection=polymer_b, material=surf_b)
        molecule.add_style("cartoon", selection=polymer_all, material=core)
        add_ligand_style(molecule, ligand, "sticks")
        return

    if preset.name.startswith("02_"):
        wire_surf = create_wire_overlay_material(
            "PR02_WireSurface",
            fill_color=(0.12, 0.61, 0.93, 1.0),
            line_color=(0.86, 0.90, 0.98, 1.0),
            roughness=0.34,
            alpha=0.62,
        )
        core = create_soft_material("PR02_Core", (0.96, 0.63, 0.34, 1.0), 0.34, 0.0, 0.12, 1.0)
        ligand = create_soft_material("PR02_Ligand", (0.72, 0.82, 0.23, 1.0), 0.18, 0.0, 0.20, 1.0)
        molecule.add_style("surface", selection=polymer_all, material=wire_surf)
        molecule.add_style("cartoon", selection=polymer_all, material=core)
        add_ligand_style(molecule, ligand, "sticks")
        return

    if preset.name.startswith("03_"):
        palette = [
            (0.95, 0.86, 0.63, 1.0),
            (0.69, 0.86, 0.98, 1.0),
            (0.95, 0.66, 0.84, 1.0),
            (0.69, 0.94, 0.76, 1.0),
            (0.98, 0.75, 0.67, 1.0),
        ]
        chunks = [
            (lo, lo + (mid - lo) // 2),
            (lo + (mid - lo) // 2 + 1, mid),
            (mid + 1, mid + (hi - mid) // 2),
            (mid + (hi - mid) // 2 + 1, hi),
        ]
        for idx, (start, end) in enumerate(chunks):
            if end < start:
                continue
            mat = create_soft_material(
                f"PR03_Cartoon_{idx}",
                palette[idx],
                roughness=0.42,
                transmission=0.0,
                coat=0.08,
                alpha=1.0,
            )
            selection = molecule.select.reset().res_id(range(start, end + 1)).is_polymer()
            molecule.add_style("cartoon", selection=selection, material=mat)
        ligand = create_soft_material("PR03_Ligand", (0.96, 0.53, 0.72, 1.0), 0.20, 0.0, 0.18, 1.0)
        add_ligand_style(molecule, ligand, "sticks")
        return

    if preset.name.startswith("04_"):
        shell_a = create_wire_overlay_material(
            "PR04_ShellA",
            fill_color=(0.84, 0.92, 0.80, 1.0),
            line_color=(0.67, 0.77, 0.61, 1.0),
            roughness=0.38,
            alpha=0.46,
        )
        shell_b = create_soft_material("PR04_ShellB", (0.93, 0.96, 0.89, 1.0), 0.42, 0.01, 0.16, 0.32)
        core_a = create_soft_material("PR04_CoreA", (0.60, 0.75, 0.28, 1.0), 0.36, 0.0, 0.06, 1.0, emission_strength=0.08)
        core_b = create_soft_material("PR04_CoreB", (0.16, 0.67, 0.77, 1.0), 0.32, 0.0, 0.08, 1.0, emission_strength=0.10)
        ligand = create_soft_material("PR04_Ligand", (0.86, 0.48, 0.19, 1.0), 0.20, 0.0, 0.12, 1.0)
        molecule.add_style("surface", selection=polymer_a, material=shell_a)
        molecule.add_style("surface", selection=polymer_b, material=shell_b)
        molecule.add_style("cartoon", selection=polymer_a, material=core_a)
        molecule.add_style("cartoon", selection=polymer_b, material=core_b)
        add_ligand_style(molecule, ligand, "sticks")
        return

    # 05 default
    surf_a = create_soft_material("PR05_SurfaceA", (0.22, 0.54, 0.82, 1.0), 0.34, 0.01, 0.22, 0.52)
    surf_b = create_soft_material("PR05_SurfaceB", (0.76, 0.43, 0.66, 1.0), 0.34, 0.01, 0.22, 0.48)
    core_a = create_soft_material("PR05_CoreA", (0.84, 0.40, 0.22, 1.0), 0.34, 0.0, 0.10, 1.0, emission_strength=0.06)
    core_b = create_soft_material("PR05_CoreB", (0.18, 0.68, 0.80, 1.0), 0.32, 0.0, 0.10, 1.0, emission_strength=0.08)
    ligand = create_soft_material("PR05_Ligand", (0.93, 0.30, 0.53, 1.0), 0.20, 0.0, 0.20, 1.0)
    molecule.add_style("surface", selection=polymer_a, material=surf_a)
    molecule.add_style("surface", selection=polymer_b, material=surf_b)
    molecule.add_style("cartoon", selection=polymer_a, material=core_a)
    molecule.add_style("cartoon", selection=polymer_b, material=core_b)
    add_ligand_style(molecule, ligand, "ball_and_stick")


def configure_render(scene: bpy.types.Scene, preset: RenderPreset) -> None:
    scene.render.engine = "BLENDER_EEVEE"
    if hasattr(scene, "eevee"):
        if hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = 128
        if hasattr(scene.eevee, "use_gtao"):
            scene.eevee.use_gtao = True
        if hasattr(scene.eevee, "gtao_distance"):
            scene.eevee.gtao_distance = 0.5
        if hasattr(scene.eevee, "use_bloom"):
            scene.eevee.use_bloom = False
    if hasattr(scene, "view_settings"):
        scene.view_settings.exposure = preset.exposure
        if hasattr(scene.view_settings, "gamma"):
            scene.view_settings.gamma = 1.0
        looks = {item.name for item in scene.view_settings.bl_rna.properties["look"].enum_items}
        scene.view_settings.look = preset.look if preset.look in looks else "None"
    scene.render.resolution_x = 1536
    scene.render.resolution_y = 1024
    scene.render.image_settings.file_format = "PNG"


def render_preset(
    preset: RenderPreset,
    scene_path: Path,
    structure_path: Path,
    out_path: Path,
) -> None:
    bpy.ops.wm.open_mainfile(filepath=str(scene_path))
    scene = bpy.context.scene
    set_world_color(scene, preset.bg)
    configure_render(scene, preset)
    clear_mesh_objects()

    molecule = mn.entities.Molecule.load(str(structure_path), name="PressReleaseMolecule")
    apply_preset_styles(preset, molecule)

    target = get_object_center(molecule.object)
    setup_three_point_lighting(scene, target, preset)
    if scene.camera is not None:
        orbit_camera(
            scene,
            target=target,
            yaw_deg=preset.camera_yaw_deg,
            pitch_deg=preset.camera_pitch_deg,
            distance_scale=preset.camera_distance_scale,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(out_path)
    bpy.ops.render.render(animation=False, write_still=True)
    print(f"Rendered preset '{preset.name}' -> {out_path}")


def main() -> None:
    scene_path, structure_path, out_dir = parse_args()
    if not scene_path.exists():
        raise FileNotFoundError(f"Scene file not found: {scene_path}")
    if not structure_path.exists():
        raise FileNotFoundError(f"Structure file not found: {structure_path}")

    for preset in PRESETS:
        out_path = out_dir / f"{preset.name}.png"
        render_preset(preset, scene_path, structure_path, out_path)


if __name__ == "__main__":
    main()
