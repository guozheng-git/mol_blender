"""Fetch and summarize macromolecular structure files with the standard library."""
from __future__ import annotations

import gzip
import json
import re
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

EXTENSIONS = {".cif": "mmcif", ".mmcif": "mmcif", ".pdb": "pdb", ".ent": "pdb", ".bcif": "bcif"}
from molecular_data import (
    WATER, ION_ELEMENTS, ADDITIVES, NUCLEIC, STANDARD, MODIFIED, COVALENT_MIN_HEAVY, ligand_key
)

PDB_ID = re.compile(r"^(?:pdb_0000)?([0-9][A-Za-z0-9]{3})$")
AFDB_ID = re.compile(r"^AF-([A-Z0-9]{6,10})-F(\d+)$", re.IGNORECASE)
UNIPROT = re.compile(r"^[OPQ][0-9][A-Z0-9]{3}[0-9]$|^[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2}$")
TOKEN = re.compile(r"'(.*?)'(?=\s|$)|\"(.*?)\"(?=\s|$)|(\S+)")
ATOM_FIELDS = {"group_PDB", "type_symbol", "label_atom_id", "auth_atom_id", "label_alt_id", "label_comp_id",
               "auth_comp_id", "label_asym_id", "auth_asym_id", "label_seq_id", "auth_seq_id",
               "pdbx_PDB_ins_code", "label_entity_id", "pdbx_PDB_model_num", "B_iso_or_equiv"}
KEEP_LOOPS = {"_atom_site", "_chem_comp", "_entity", "_entity_src_gen"}


class DownloadBlocked(RuntimeError):
    """Every mirror failed, usually because this sandbox restricts network egress."""


@dataclass(frozen=True)
class Structure:
    """One source snapshot and its summary, reusable throughout workspace creation."""

    data: bytes
    name: str
    summary: dict

    @classmethod
    def load(cls, path: str | Path) -> Structure:
        data, name = read_structure_bytes(path)
        return cls(data, name, summarize(data, name))


def format_of(name: str) -> str:
    suffix = Path(name[:-3] if name.lower().endswith(".gz") else name).suffix.lower()
    if suffix not in EXTENSIONS:
        raise ValueError(f"Unsupported structure file {name!r}; use .cif, .mmcif, .pdb, .ent or .bcif (optionally .gz)")
    return EXTENSIONS[suffix]


def sniff(data: bytes) -> str:
    """'mmcif' or 'pdb' from file content (for copies saved under another name, such as <asset id>.txt)."""
    head = data[:4096].decode("utf-8", errors="replace")
    if re.search(r"^data_|^_atom_site\.|^loop_", head, re.M):
        return "mmcif"
    if re.search(r"^(ATOM  |HETATM|HEADER|CRYST1|MODEL )", head, re.M):
        return "pdb"
    raise ValueError("Could not tell whether this text is mmCIF or PDB")


def read_structure_bytes(path: str | Path) -> tuple[bytes, str]:
    """Raw (decompressed) file bytes and the filename without any .gz suffix.

    A ``.txt`` copy (the workspace's stored structure) keeps its inner name when it has one
    (``9lhj.cif.txt``) and is otherwise named from its content (``<id>.cif``).
    """
    source = Path(path).expanduser()
    data = source.read_bytes()
    name = source.name
    if name.lower().endswith(".gz"):
        data, name = gzip.decompress(data), name[:-3]
    if name.lower().endswith(".txt"):
        inner = name[:-4]
        name = inner if Path(inner).suffix.lower() in EXTENSIONS else inner + (".cif" if sniff(data) == "mmcif" else ".pdb")
    format_of(name)
    return data, name


def mirrors(identifier: str) -> list[tuple[str, str]]:
    """Candidate (url, filename) pairs for a PDB or AlphaFold DB identifier."""
    text = identifier.strip()
    match = PDB_ID.match(text)
    if match:
        code = match.group(1).lower()
        return [(f"https://files.rcsb.org/download/{code}.cif.gz", f"{code}.cif.gz"),
                (f"https://www.ebi.ac.uk/pdbe/entry-files/download/{code}.cif", f"{code}.cif"),
                (f"https://files.pdbj.org/pub/pdb/data/structures/divided/mmCIF/{code[1:3]}/{code}.cif.gz", f"{code}.cif.gz")]
    match = AFDB_ID.match(text)
    accession = match.group(1).upper() if match else (text.upper() if UNIPROT.match(text.upper()) else None)
    if accession:
        fragment = match.group(2) if match else "1"
        # File versions change over time; the prediction API names the current one.
        return [(f"https://alphafold.ebi.ac.uk/api/prediction/{accession}#F{fragment}", "")] + [
            (f"https://alphafold.ebi.ac.uk/files/AF-{accession}-F{fragment}-model_v{v}.cif",
             f"AF-{accession}-F{fragment}-model_v{v}.cif") for v in (6, 4)]
    raise ValueError(f"{identifier!r} is neither a file nor a PDB / AlphaFold DB identifier")


def fetch(identifier: str, directory: str, timeout: float = 30) -> dict:
    """Use an existing local file, or download an identifier from the first reachable mirror."""
    local = Path(identifier).expanduser()
    if local.is_file():
        format_of(local.name)
        return {"path": str(local.resolve()), "source": "local file", "downloaded": False}
    destination_dir = Path(directory).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    for url, filename in mirrors(identifier):
        if "/api/prediction/" in url:
            try:
                api, _, fragment = url.partition("#")
                with urllib.request.urlopen(api, timeout=timeout) as response:
                    entries = json.load(response)
                entry = next(e for e in entries if e.get("entryId", "").endswith("-" + fragment))
                url = entry["cifUrl"]
                filename = url.rsplit("/", 1)[-1]
            except (urllib.error.URLError, OSError, ValueError, StopIteration, KeyError, TimeoutError) as exc:
                errors.append(f"{api}: {getattr(exc, 'reason', exc)}")
                continue
        destination = destination_dir / filename
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "structure-studio/0.1"})
            with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            if destination.stat().st_size < 200:
                raise ValueError("response too small to be a structure")
            if destination.suffix == ".gz":
                gzip.decompress(destination.read_bytes())
            return {"path": str(destination.resolve()), "source": url, "downloaded": True}
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            destination.unlink(missing_ok=True)
            errors.append(f"{url}: {getattr(exc, 'reason', exc)}")
    raise DownloadBlocked("Could not download " + identifier + ". Tried:\n  " + "\n  ".join(errors))


def _cif_tables(text: str) -> tuple[dict, dict]:
    items: dict[str, str] = {}
    loops: dict[str, tuple[list[str], list[list[str] | None]]] = {}
    mode, tags, columns, keep, index, pending, blocks = 0, [], [], False, 0, None, 0

    def finish():
        nonlocal mode, tags, columns
        if mode and keep and tags:
            loops[tags[0].split(".")[0]] = (tags, columns)
        mode, tags, columns = 0, [], []

    def value(token):
        nonlocal mode, keep, columns, index, pending
        if pending:
            items[pending] = token
            pending = None
            return
        if mode == 1:
            mode = 2
            category = tags[0].split(".")[0]
            keep = category in KEEP_LOOPS or category.startswith("_ma_qa_metric")
            columns = [[] if keep and (category != "_atom_site" or tag.split(".")[1] in ATOM_FIELDS) else None
                       for tag in tags]
            index = 0
        if mode == 2:
            column = columns[index]
            if column is not None:
                column.append(token)
            index = (index + 1) % len(tags)

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(";"):
            block = [line[1:]]
            i += 1
            while i < len(lines) and not lines[i].startswith(";"):
                block.append(lines[i])
                i += 1
            value("\n".join(block).strip())
            i += 1
            continue
        for match in TOKEN.finditer(line):
            token = match.group(3)
            if token is None:
                value(match.group(1) if match.group(1) is not None else match.group(2))
                continue
            if token.startswith("#"):
                break
            if token.startswith("_"):
                if mode == 1:
                    tags.append(token)
                    continue
                if mode == 2:
                    finish()
                pending = token
                continue
            if token == "loop_":
                finish()
                mode = 1
                continue
            if token.startswith("data_"):
                blocks += 1
                if blocks > 1:
                    finish()
                    return items, loops
                continue
            value(token)
        i += 1
    finish()
    return items, loops


def _column(loops, category, field):
    if category not in loops:
        return None
    tags, columns = loops[category]
    for tag, column in zip(tags, columns):
        if tag == f"{category}.{field}":
            return column
    return None


def _atoms_from_cif(text: str):
    items, loops = _cif_tables(text)
    if "_atom_site" not in loops:
        raise ValueError("No _atom_site coordinates in this mmCIF file")
    col = lambda f, alt=None: _column(loops, "_atom_site", f) or (alt and _column(loops, "_atom_site", alt))
    count = len(col("type_symbol") or col("label_atom_id"))
    models, alts = col("pdbx_PDB_model_num"), col("label_alt_id")
    groups, elements = col("group_PDB"), col("type_symbol")
    comps, chains = col("auth_comp_id", "label_comp_id"), col("auth_asym_id", "label_asym_id")
    seqs, label_seqs, labels = col("auth_seq_id", "label_seq_id"), col("label_seq_id"), col("label_asym_id")
    names, inserts, entities = col("auth_atom_id", "label_atom_id"), col("pdbx_PDB_ins_code"), col("label_entity_id")
    atoms = []
    for i in range(count):
        if models and models[i] != models[0]:
            continue
        if alts and alts[i] not in {".", "?", "A", "1"}:
            continue
        label_seq = label_seqs[i] if label_seqs else "."
        atoms.append({"het": bool(groups and groups[i] == "HETATM"), "element": (elements[i] if elements else "").upper(),
                      "name": names[i], "comp": comps[i], "chain": chains[i], "seq": seqs[i],
                      "ins": inserts[i] if inserts and inserts[i] not in {".", "?"} else "",
                      "label_asym": labels[i] if labels else chains[i], "polymer_seq": label_seq not in {".", "?"},
                      "entity": entities[i] if entities else ""})
    comp_names = dict(zip(_column(loops, "_chem_comp", "id") or [], _column(loops, "_chem_comp", "name") or []))
    if "_chem_comp.id" in items:
        comp_names[items["_chem_comp.id"]] = items.get("_chem_comp.name", "")
    entity_names = dict(zip(_column(loops, "_entity", "id") or [], _column(loops, "_entity", "pdbx_description") or []))
    if "_entity.id" in items:
        entity_names[items["_entity.id"]] = items.get("_entity.pdbx_description", "")
    genes = dict(zip(_column(loops, "_entity_src_gen", "entity_id") or [], _column(loops, "_entity_src_gen", "pdbx_gene_src_gene") or []))
    if "_entity_src_gen.entity_id" in items:
        genes[items["_entity_src_gen.entity_id"]] = items.get("_entity_src_gen.pdbx_gene_src_gene", "")
    genes = {k: re.split(r"[,;\s]+", v.strip())[0] for k, v in genes.items() if v and v not in {"?", "."}}
    resolution = (items.get("_refine.ls_d_res_high") or items.get("_em_3d_reconstruction.resolution")
                  or items.get("_reflns.d_resolution_high"))
    meta = {"id": items.get("_entry.id", ""), "title": items.get("_struct.title", ""),
            "method": items.get("_exptl.method", ""),
            "resolution": float(resolution) if resolution and re.match(r"^[\d.]+$", resolution) else None,
            "has_plddt_metrics": any(k.startswith("_ma_qa_metric") for k in list(loops) + list(items)),
            "genes": genes}
    return atoms, comp_names, entity_names, meta


def _atoms_from_pdb(text: str):
    atoms, names, title, meta = [], {}, [], {"id": "", "method": "", "resolution": None, "has_plddt_metrics": False}
    for line in text.splitlines():
        record = line[:6]
        if record == "ENDMDL":
            break
        if record in {"ATOM  ", "HETATM"}:
            if len(line) > 16 and line[16] not in {" ", "A", "1"}:
                continue
            name = line[12:16].strip()
            element = (line[76:78].strip() or re.sub(r"[^A-Za-z]", "", name)[:1]).upper()
            chain = line[21:22].strip()
            atoms.append({"het": record == "HETATM", "element": element, "name": name, "comp": line[17:20].strip(),
                          "chain": chain, "seq": line[22:26].strip(), "ins": line[26].strip() if len(line) > 26 else "",
                          "label_asym": chain, "polymer_seq": None, "entity": ""})
        elif record == "HEADER":
            meta["id"] = line[62:66].strip()
        elif record == "TITLE ":
            title.append(line[10:].strip())
        elif record == "HETNAM":
            key = line[11:14].strip()
            names[key] = (names.get(key, "") + " " + line[15:].strip()).strip()
        elif record == "EXPDTA":
            meta["method"] = line[10:].strip()
        elif record == "REMARK" and line[6:10] == "   2" and "RESOLUTION." in line:
            match = re.search(r"([\d.]+)\s+ANGSTROM", line)
            meta["resolution"] = float(match.group(1)) if match else None
    meta["title"] = " ".join(title)
    return atoms, names, {}, meta


def _short_title(title: str, limit: int = 40) -> str:
    """Sentence-case an all-caps PDB title and cut it at a word boundary."""
    text = title.strip()
    if text.isupper():
        text = text[:1] + text[1:].lower()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0]
    return text.rstrip(" .,;:-")


def summarize(data: bytes, name: str) -> dict:
    """Chains, ligands and headline metadata for answering questions and labeling pages."""
    fmt = format_of(name)
    if fmt == "bcif":
        return {"id": Path(name).stem, "title": "", "format": fmt, "note": "BinaryCIF is viewable but not summarized"}
    text = data.decode("utf-8", errors="replace")
    atoms, comp_names, entity_names, meta = (_atoms_from_cif if fmt == "mmcif" else _atoms_from_pdb)(text)
    residues: list[dict] = []
    for atom in atoms:
        key = (atom["chain"], atom["seq"], atom["ins"], atom["comp"], atom["label_asym"])
        if not residues or residues[-1]["key"] != key:
            residues.append({"key": key, "atoms": [], "names": set(), **{k: atom[k] for k in
                            ("chain", "seq", "ins", "comp", "het", "polymer_seq", "entity")}})
        residues[-1]["atoms"].append(atom)
        residues[-1]["names"].add(atom["name"])
    chains: dict[str, dict] = {}
    ligands, waters, ions = [], 0, []
    for residue in residues:
        backbone = {"N", "CA", "C"} <= residue["names"] or {"P", "C4'"} <= residue["names"]
        # PyMOL-written mmCIF numbers ligands like chain residues, so HETATM residues also need a backbone.
        polymer = (not residue["het"] or backbone) if residue["polymer_seq"] is None else (residue["polymer_seq"] and (not residue["het"] or backbone))
        chain = chains.setdefault(residue["chain"], {"id": residue["chain"], "residues": 0, "nucleic": 0, "description": "",
                                                     "gene": meta.get("genes", {}).get(residue["entity"], "")})
        heavy = sum(a["element"] not in {"H", "D"} for a in residue["atoms"])
        covalent = polymer and residue["comp"] not in STANDARD + MODIFIED and heavy >= COVALENT_MIN_HEAVY
        if covalent:
            chain["residues"] += 1
            polymer = False
        if residue["comp"] in WATER:
            waters += 1
        elif polymer:
            chain["residues"] += 1
            chain["nucleic"] += residue["comp"] in NUCLEIC
            chain["description"] = chain["description"] or entity_names.get(residue["entity"], "")
        elif len(residue["atoms"]) == 1 and residue["atoms"][0]["element"] in ION_ELEMENTS:
            ions.append(f"{residue['comp']} {residue['chain']}{residue['seq']}{residue['ins']}")
        else:
            ligands.append({"key": ligand_key(residue['comp'], residue['chain'], residue['seq'], residue['ins']),
                            "comp": residue["comp"], "name": comp_names.get(residue["comp"], ""),
                            "heavy_atoms": heavy, "additive": residue["comp"] in ADDITIVES, "covalent": covalent})
    ligands.sort(key=lambda item: (item["additive"], -item["heavy_atoms"]))
    chain_list = [{"id": c["id"], "residues": c["residues"],
                   "type": "nucleic acid" if c["nucleic"] > c["residues"] / 2 else "protein",
                   "description": c["description"], "gene": c["gene"]} for c in chains.values() if c["residues"]]
    hint = f"{name} {meta['title']}".lower()
    predicted = meta["has_plddt_metrics"] or bool(re.search(r"(^|[/_\s-])(af-|fold_|alphafold|esmfold|boltz|chai)", hint))
    title = meta["title"] or next((c["description"] for c in chain_list if c["description"]), "")
    entry = meta["id"] or Path(name).stem
    genes = list(dict.fromkeys(c["gene"].upper() for c in chain_list if c["gene"]))
    label = "–".join(genes[:3]) if genes else _short_title(title)
    return {"id": entry, "title": title, "suggested_workspace_title": f"{label} · {entry}" if label else entry,
            "format": fmt, "method": meta["method"],
            "resolution_angstrom": meta["resolution"], "atoms": len(atoms), "chains": chain_list,
            "ligands": ligands, "ions": ions, "waters": waters, "likely_predicted_model": predicted}
