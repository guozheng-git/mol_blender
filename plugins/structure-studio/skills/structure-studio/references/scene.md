# Scene reference

`scene/current` is one JSON object. The panel draws it, and `pymol_session.py --scene` reproduces it for PyMOL and Blender. `studio.py scene` validates it and applies edits; `--structure FILE` also checks chain ids and ligands.

| Field | Default | Meaning |
| --- | --- | --- |
| `schema` | `1` | Version of this format. |
| `representation` | `"cartoon"` | Polymer style: `cartoon`, `surface`, `spacefill`, `putty`, `backbone`, `hidden`. |
| `color` | `"chain"` | `chain`, `ss` (secondary structure), `rainbow` (N→C per chain), `bfactor`, `plddt` (AlphaFold bins), `element`, or `#rrggbb`. |
| `chain_colors` | set at creation | `{"A": "#4bb5a6", …}`. The panel, PyMOL and Blender use the same colors, and hiding a chain never recolors the others. |
| `opacity` | `1` | Polymer opacity, 0.05–1. Binding-site views use 0.45. |
| `hidden` | `[]` | Selections removed from view. In renders they move to a disabled `studio_hidden` object, kept in the `.pse` but not drawn. |
| `ligands`, `ions`, `waters` | `true`, `true`, `false` | Show these groups. Crystallization additives are never drawn as ligands. |
| `layers` | `[]` | Extra drawings on top: `{"select": …, "representation": "sticks", "color": "element", "label": true, "opacity": 0.8}`. Representations: `sticks`, `spacefill`, `surface`, `cartoon`, `putty`. `label` is `true` for residue labels (up to 40) or a text label. |
| `focus` | `null` | A selection to zoom to. The panel refocuses only when this changes. |
| `note`, `revision`, `updated_by`, `written_at` | | Bookkeeping, set by `studio.py scene` or the page. |

## Selections

An object whose fields combine with AND:

| Field | Example | Matches |
| --- | --- | --- |
| `chain` | `"A"` or `["A", "B"]` | Author chain ids |
| `residues` | `"45-50,60,-3--1,27A"` | Author residue numbers (insertion codes allowed) |
| `ligand` | `"HEM A142"` or `"HEM"` | One ligand instance, or every instance of a residue name |
| `site` | `"A1EJS B74"` | Residues within `cutoff` Å (default 5) of the ligand, excluding the ligand |
| `kind` | `"protein"` | `all`, `polymer`, `protein`, `nucleic`, `ligand`, `water`, `ion` |

Ligand keys come from `studio.py info` (`RESN CHAINRESI`, e.g. `HEM A142`). Covalent inhibitors built into a chain (non-standard residues with at least 15 heavy atoms) count as ligands, e.g. `A1EJS B74` in 9LHJ.

## Examples

```bash
# Hide chain A, then show the chain-B inhibitor's pocket with labels
python3 "$S" scene current.json --hide-chain A --site "A1EJS B74" --label-site --note "hide A, B inhibitor site" -o next.json
# Highlight residues 45–50 of chain B in magenta with labels
python3 "$S" scene current.json --patch '{"layers": [{"select": {"chain": "B", "residues": "45-50"}, "representation": "sticks", "color": "#d0459b", "label": true}]}' -o next.json
# Surface colored by secondary structure, waters on
python3 "$S" scene current.json --style surface --color ss --patch '{"waters": true}' -o next.json
```

`--patch` replaces each field it names, so to add to `hidden` or `layers` include the existing entries. Prefer the shortcut flags, which merge for you.
