# SPDX-FileCopyrightText: 2026 Magnus Bauer
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared Cycles device selection for constructed and saved-scene renders."""
import json
import os
import bpy
from mol_blender.renderer_defaults import resolve_renderer


def _configure_cycles_gpu_for_custom(scene, renderer) -> None:
    renderer = resolve_renderer(renderer)
    requested = str(renderer["compute_device_type"]).upper()
    want_gpu = scene.cycles.device == "GPU"
    addon = bpy.context.preferences.addons.get("cycles")
    candidates = ["METAL", "CUDA", "OPTIX", "HIP", "ONEAPI"] if requested == "AUTO" else [requested]
    selected, devices = None, []
    if addon and want_gpu:
        prefs = addon.preferences
        for candidate in candidates:
            try:
                prefs.compute_device_type = candidate
                prefs.get_devices()
                available = [d for d in prefs.devices if d.type == candidate]
                if not available:
                    continue
                for device in prefs.devices:
                    device.use = device.type == candidate
                selected = candidate
                devices = [{"name":d.name,"type":d.type} for d in available]
                break
            except (TypeError, RuntimeError):
                continue
    if want_gpu and not devices:
        if bool(renderer.get("require_gpu", os.getenv("PRESS_REQUIRE_GPU", "").strip().lower() in {"1", "true", "yes", "on"})):
            raise RuntimeError("No usable GPU found for the requested Cycles backend")
        scene.cycles.device = "CPU"
    info = {"device":scene.cycles.device,"compute_type":selected,"devices":devices}
    scene["bridge_device_info"] = json.dumps(info)
    print("Cycles device: " + json.dumps(info), flush=True)
