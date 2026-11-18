"""Sequence-ordered sRGB palettes, independent of molecule orientation."""
from __future__ import annotations


def sequence_colors(chains, residues, elements, palettes, element_palette):
    import numpy as np

    def rgb(value):
        return np.array([int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)])

    def linear(value):
        return np.where(value <= .04045, value / 12.92, ((value + .055) / 1.055) ** 2.4)

    ordered = {}
    for chain, residue in zip(chains, residues):
        ordered.setdefault(str(chain), {})
        if residue not in ordered[str(chain)]:
            ordered[str(chain)][residue] = len(ordered[str(chain)])
    colors = np.ones((len(chains), 4), dtype=np.float32)
    for i, (chain, residue) in enumerate(zip(chains, residues)):
        chain = str(chain)
        palette = palettes.get(chain, palettes.get('*'))
        if palette is None:
            raise ValueError(f'No sequence palette for chain {chain!r}; provide it or a * fallback')
        position = ordered[chain][residue] / max(1, len(ordered[chain]) - 1) * (len(palette) - 1)
        low = min(int(position), len(palette) - 2)
        weight = position - low
        colors[i, :3] = linear(rgb(palette[low]) * (1 - weight) + rgb(palette[low + 1]) * weight)
    mixed = colors.copy()
    for i, element in enumerate(elements):
        if str(element).upper() != 'C':
            mixed[i, :3] = linear(rgb(element_palette.get(str(element).upper(), '#808080')))
    return colors, mixed
