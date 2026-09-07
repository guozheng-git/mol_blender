#!/usr/bin/env python3
"""Helpers for expanding polymer styles into per-chain colored variants."""

from __future__ import annotations

import colorsys
import json
import re
from typing import Any

HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")


def _normalize_chain_id(value: Any) -> str:
    text = str(value or "").strip().upper()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip().upper()
    if not text or text in {".", "?"}:
        return ""
    return text


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    raw = str(hex_color).strip().lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"Expected #RRGGBB color, got {hex_color!r}")
    return (
        int(raw[0:2], 16) / 255.0,
        int(raw[2:4], 16) / 255.0,
        int(raw[4:6], 16) / 255.0,
    )


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r = max(0.0, min(1.0, float(rgb[0])))
    g = max(0.0, min(1.0, float(rgb[1])))
    b = max(0.0, min(1.0, float(rgb[2])))
    return f"#{int(round(r * 255.0)):02X}{int(round(g * 255.0)):02X}{int(round(b * 255.0)):02X}"


def _mix_rgb(left: tuple[float, float, float], right: tuple[float, float, float], mix: float) -> tuple[float, float, float]:
    w = max(0.0, min(1.0, float(mix)))
    return (
        left[0] * (1.0 - w) + right[0] * w,
        left[1] * (1.0 - w) + right[1] * w,
        left[2] * (1.0 - w) + right[2] * w,
    )


def _shade_from_base(rgb: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    f = max(0.0, min(1.0, float(factor)))
    return (rgb[0] * f, rgb[1] * f, rgb[2] * f)


def _highlight_from_base(rgb: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    f = max(0.0, float(factor))
    return (
        min(1.0, rgb[0] + (1.0 - rgb[0]) * f),
        min(1.0, rgb[1] + (1.0 - rgb[1]) * f),
        min(1.0, rgb[2] + (1.0 - rgb[2]) * f),
    )


def ordered_chain_ids_for_palette(chain_ids: list[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in chain_ids:
        chain_id = _normalize_chain_id(raw)
        if not chain_id or chain_id in seen:
            continue
        seen.add(chain_id)
        ordered.append(chain_id)
    prioritized = [chain_id for chain_id in ("A", "B") if chain_id in seen]
    remainder = [chain_id for chain_id in ordered if chain_id not in {"A", "B"}]
    return prioritized + remainder


def style_targets_polymer(style_cfg: dict[str, Any]) -> bool:
    selection = style_cfg.get("selection")
    if isinstance(selection, dict):
        if bool(selection.get("polymer")):
            return True
        query = str(selection.get("query", "")).strip().lower()
        if "polymer" in query and "not polymer" not in query:
            return True
    style_name = str(style_cfg.get("name", "")).strip().lower()
    if style_name.startswith("chain_"):
        return True
    if "polymer" in style_name:
        return True
    return False


def selection_chain_constraints(selection_cfg: dict[str, Any]) -> tuple[set[str], set[str]]:
    include: set[str] = set()
    exclude: set[str] = set()

    chain_value = selection_cfg.get("chain")
    if isinstance(chain_value, str):
        include.update(_normalize_chain_id(item) for item in chain_value.split(","))
    elif isinstance(chain_value, (list, tuple)):
        include.update(_normalize_chain_id(item) for item in chain_value)
    include.discard("")

    query = str(selection_cfg.get("query", "") or "")
    for match in re.finditer(r"(?:(\bnot\b|!)\s*)?\bchain\s*=?\s*([A-Za-z0-9_,]+)", query, flags=re.IGNORECASE):
        negate = bool(match.group(1))
        values = [item for item in (_normalize_chain_id(part) for part in match.group(2).split(",")) if item]
        if negate:
            exclude.update(values)
        else:
            include.update(values)
    return include, exclude


def selection_with_chain_filter(selection_cfg: dict[str, Any], chain_id: str) -> dict[str, Any]:
    updated = json.loads(json.dumps(selection_cfg))
    updated["chain"] = _normalize_chain_id(chain_id)
    return updated


def _style_material_base_hex(materials_cfg: dict[str, dict[str, Any]], material_name: str) -> str | None:
    material_cfg = materials_cfg.get(material_name)
    if not isinstance(material_cfg, dict):
        return None
    base_hex = str(material_cfg.get("base_color", "")).strip()
    if not HEX_COLOR_RE.fullmatch(base_hex):
        return None
    return base_hex.upper()


def _derive_companion_chain_color(anchor_hex: str) -> str:
    rgb = _hex_to_rgb(anchor_hex)
    h, s, v = colorsys.rgb_to_hsv(*rgb)
    h = (h - 0.20) % 1.0
    s = max(0.30, min(0.82, s * 0.95 + 0.06))
    v = max(0.46, min(0.95, v * 1.02))
    return _rgb_to_hex(colorsys.hsv_to_rgb(h, s, v))


def _resolve_polymer_chain_anchor_colors(
    style_doc: dict[str, Any],
    materials_cfg: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    styles = style_doc.get("styles")
    if not isinstance(styles, list):
        return "#4EAAD9", "#66DAB6"

    anchor_a: str | None = None
    anchor_b: str | None = None
    polymer_materials: list[str] = []

    for style_cfg in styles:
        if not isinstance(style_cfg, dict) or not style_targets_polymer(style_cfg):
            continue
        material_name = str(style_cfg.get("material", "")).strip()
        if material_name and material_name not in polymer_materials:
            polymer_materials.append(material_name)
        style_name = str(style_cfg.get("name", "")).strip().lower()
        if not material_name:
            continue
        base_hex = _style_material_base_hex(materials_cfg, material_name)
        if base_hex is None:
            continue
        if anchor_a is None and "chain_a" in style_name:
            anchor_a = base_hex
        if anchor_b is None and "chain_b" in style_name:
            anchor_b = base_hex

    if anchor_a is None:
        for material_name in polymer_materials:
            base_hex = _style_material_base_hex(materials_cfg, material_name)
            if base_hex:
                anchor_a = base_hex
                break
    if anchor_a is None:
        anchor_a = "#4EAAD9"

    if anchor_b is None:
        anchor_b = _derive_companion_chain_color(anchor_a)

    return anchor_a, anchor_b


def _build_harmonized_chain_palette(anchor_a: str, anchor_b: str, count: int) -> list[str]:
    if count <= 0:
        return []

    base_a = _hex_to_rgb(anchor_a)
    base_b = _hex_to_rgb(anchor_b)
    palette: list[str] = [_rgb_to_hex(base_a)]
    if count == 1:
        return palette

    palette.append(_rgb_to_hex(base_b))
    golden = 0.61803398875
    for idx in range(2, count):
        ratio = (idx - 1) / max(1, count - 1)
        blended = _mix_rgb(base_a, base_b, mix=max(0.12, min(0.88, ratio)))
        h, s, v = colorsys.rgb_to_hsv(*blended)
        hue = (h + (idx - 1) * golden * 0.18) % 1.0
        sat = max(0.28, min(0.85, s * 0.92 + 0.06))
        val = max(0.45, min(0.96, v * 1.04 + 0.01))
        candidate = _rgb_to_hex(colorsys.hsv_to_rgb(hue, sat, val))
        if candidate in palette:
            candidate = _rgb_to_hex(colorsys.hsv_to_rgb((hue + 0.07) % 1.0, sat, val))
        palette.append(candidate)
    return palette


def _chain_material_variant_name(base_material_name: str, chain_id: str) -> str:
    safe_chain = re.sub(r"[^A-Za-z0-9_]+", "_", _normalize_chain_id(chain_id)).strip("_")
    if not safe_chain:
        safe_chain = "CHAIN"
    return f"{base_material_name}__chain_{safe_chain}"


def _clone_material_for_chain_color(base_material_cfg: dict[str, Any], base_hex: str) -> dict[str, Any]:
    material_cfg = json.loads(json.dumps(base_material_cfg))
    rgb = _hex_to_rgb(base_hex)
    material_cfg["base_color"] = _rgb_to_hex(rgb)
    material_cfg["shadow_color"] = _rgb_to_hex(_shade_from_base(rgb, 0.72))
    material_cfg["highlight_color"] = _rgb_to_hex(_highlight_from_base(rgb, 0.20))
    return material_cfg


def expand_polymer_styles_for_multichain_palette(
    *,
    style_doc: dict[str, Any],
    materials_cfg: dict[str, dict[str, Any]],
    protein_chain_ids: list[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
    styles = style_doc.get("styles")
    if not isinstance(styles, list):
        return [], materials_cfg, {}

    ordered_chain_ids = ordered_chain_ids_for_palette(protein_chain_ids)
    if len(ordered_chain_ids) <= 1:
        return list(styles), materials_cfg, {}

    anchor_a, anchor_b = _resolve_polymer_chain_anchor_colors(style_doc, materials_cfg)
    palette = _build_harmonized_chain_palette(anchor_a, anchor_b, len(ordered_chain_ids))
    chain_colors = {
        chain_id: palette[idx]
        for idx, chain_id in enumerate(ordered_chain_ids)
    }

    runtime_materials = json.loads(json.dumps(materials_cfg))
    expanded_styles: list[dict[str, Any]] = []
    variant_name_cache: dict[tuple[str, str], str] = {}

    for style_cfg in styles:
        if not isinstance(style_cfg, dict):
            continue
        style_copy = json.loads(json.dumps(style_cfg))
        if not style_targets_polymer(style_copy) or not style_copy.get("color_by_chain", True):
            expanded_styles.append(style_copy)
            continue

        selection_cfg = style_copy.get("selection")
        if not isinstance(selection_cfg, dict):
            expanded_styles.append(style_copy)
            continue

        include_chains, exclude_chains = selection_chain_constraints(selection_cfg)
        if include_chains:
            expanded_styles.append(style_copy)
            continue

        target_chains = [chain_id for chain_id in ordered_chain_ids if chain_id not in exclude_chains]
        if not target_chains:
            expanded_styles.append(style_copy)
            continue

        base_material_name = str(style_copy.get("material", "")).strip()
        if not base_material_name or base_material_name not in runtime_materials:
            expanded_styles.append(style_copy)
            continue

        style_name = str(style_copy.get("name", "")).strip() or "polymer_style"
        for chain_id in target_chains:
            variant_key = (base_material_name, chain_id)
            variant_name = variant_name_cache.get(variant_key)
            if variant_name is None:
                variant_name = _chain_material_variant_name(base_material_name, chain_id)
                variant_name_cache[variant_key] = variant_name
                runtime_materials[variant_name] = _clone_material_for_chain_color(
                    runtime_materials[base_material_name],
                    chain_colors[chain_id],
                )

            chain_style = json.loads(json.dumps(style_copy))
            chain_style["name"] = f"{style_name}__chain_{chain_id}"
            chain_style["material"] = variant_name
            chain_style["selection"] = selection_with_chain_filter(selection_cfg, chain_id)
            fallback_selection = chain_style.get("fallback_selection")
            if isinstance(fallback_selection, dict):
                chain_style["fallback_selection"] = selection_with_chain_filter(fallback_selection, chain_id)
            expanded_styles.append(chain_style)

    return expanded_styles, runtime_materials, chain_colors
