"""Structure metadata extraction using gemmi."""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import gemmi
import numpy as np

# Map AF3/MA-format `_ma_qa_metric.name` values to StructureRecord field names.
GLOBAL_METRIC_MAP = {"pTM": "ptm", "ipTM": "iptm", "ranking_score": "ranking_score"}

# AF3 Server stems look like `<base>_model_N`; capture (base, N) to find siblings.
AF3_MODEL_STEM_RE = re.compile(r"(.+)_model_(\d+)$")


@dataclass
class StructureRecord:
    name: str
    path: Path
    chains: list[str]
    n_residues: int
    plddt: np.ndarray | None = field(default=None, repr=False)  # per-residue, 0-100
    pae: np.ndarray | None = field(default=None, repr=False)
    iptm: float | None = None
    ptm: float | None = None
    ranking_score: float | None = None  # AF3-only top-line score
    # Auth chain, residue number and insertion code, in the same order as pLDDT.
    residue_ids: list[tuple[str, int, str]] = field(default_factory=list, repr=False)

    @property
    def mean_plddt(self) -> float | None:
        if self.plddt is not None and len(self.plddt) > 0:
            return float(np.mean(self.plddt))
        return None

    def sort_key(self) -> float:
        """Sortable pLDDT (missing → -inf so unscored records sink to the bottom)."""
        return self.mean_plddt if self.mean_plddt is not None else float("-inf")

    @property
    def plddt_summary(self) -> str:
        if self.plddt is None:
            return "no pLDDT"
        mean = self.mean_plddt
        high = float(np.sum(self.plddt >= 90) / len(self.plddt) * 100)
        low = float(np.sum(self.plddt < 50) / len(self.plddt) * 100)
        return (
            f"mean={mean:.1f}, {high:.0f}% very high (>=90), {low:.0f}% very low (<50)"
        )

    def format_report(self) -> str:
        lines = [
            f"Structure: {self.name}",
            f"  File: {self.path.name}",
            f"  Chains: {', '.join(self.chains)}",
            f"  Residues: {self.n_residues}",
        ]
        if self.plddt is not None:
            lines.append(f"  pLDDT: {self.plddt_summary}")
            lines.append(
                "    Interpretation: "
                ">90 very high confidence, "
                "70-90 confident, "
                "50-70 low confidence, "
                "<50 very low"
            )
        if self.ptm is not None:
            lines.append(f"  pTM: {self.ptm:.3f}")
        if self.iptm is not None:
            lines.append(f"  ipTM: {self.iptm:.3f}")
            lines.append(
                "    Interpretation: "
                ">0.8 confident interaction, "
                "0.6-0.8 possible, "
                "<0.6 unlikely"
            )
        if self.ranking_score is not None:
            lines.append(f"  ranking_score: {self.ranking_score:.3f}")
        if self.pae is not None:
            lines.append(f"  PAE: mean={float(np.nanmean(self.pae)):.1f} A")
        return "\n".join(lines)


def metrics_from_cif(path: Path) -> dict:
    """Read PAE and global QA metrics embedded in mmCIF (`_ma_qa_metric_*`)."""
    if path.suffix.lower() not in (".cif", ".mmcif"):
        return {}

    try:
        block = gemmi.cif.read(str(path)).sole_block()
    except (RuntimeError, ValueError):
        return {}

    out: dict = {}

    # Build metric_id → (name, type) so we can look up by either field.
    metric_info = {
        row[0]: (row[1], row[2])
        for row in block.find("_ma_qa_metric.", ["id", "name", "type"])
    }
    if any(name.lower() == "plddt" or kind.lower() == "plddt"
           for name, kind in metric_info.values()):
        out["plddt_in_b_factors"] = True

    for row in block.find("_ma_qa_metric_global.", ["metric_id", "metric_value"]):
        info = metric_info.get(row[0])
        if info is None:
            continue
        key = GLOBAL_METRIC_MAP.get(info[0])
        if key is None:
            continue
        with contextlib.suppress(ValueError):
            out[key] = float(row[1])

    # Identify the PAE metric_id by name aliases or type — predictor-dependent.
    pae_metric_id = None
    for mid, (name, mtype) in metric_info.items():
        n, t = name.lower(), mtype.lower()
        if "pae" in n or "aligned error" in n or t == "pae":
            pae_metric_id = mid
            break

    if pae_metric_id is not None:
        pae_rows = []
        for row in block.find(
            "_ma_qa_metric_local_pairwise.",
            [
                "label_asym_id_1",
                "seq_id_1",
                "label_asym_id_2",
                "seq_id_2",
                "metric_value",
                "metric_id",
            ],
        ):
            if row[5] != pae_metric_id:
                continue
            try:
                pae_rows.append(
                    (row[0], int(row[1]), row[2], int(row[3]), float(row[4]))
                )
            except ValueError:
                continue

        if pae_rows:
            residues = sorted(
                {(c, s) for c, s, _, _, _ in pae_rows}
                | {(c, s) for _, _, c, s, _ in pae_rows}
            )
            idx = {r: i for i, r in enumerate(residues)}
            pae = np.full((len(residues), len(residues)), np.nan)
            for c1, s1, c2, s2, v in pae_rows:
                pae[idx[(c1, s1)], idx[(c2, s2)]] = v
            # Some predictors only store the upper triangle; fill the missing
            # transpose. PAE is asymmetric, so only fill where the cell is NaN.
            nan_mask = np.isnan(pae)
            pae[nan_mask] = pae.T[nan_mask]
            out["pae"] = pae

    return out


def _af3_base_and_index(stem: str) -> tuple[str | None, str | None]:
    """Split an AF3 Server `<base>_model_N` stem into (base, N), else (None, None)."""
    af3 = AF3_MODEL_STEM_RE.match(stem)
    return (af3.group(1), af3.group(2)) if af3 else (None, None)


def sibling_candidates(
    path: Path, af3_suffix: str, stem_patterns: list[str]
) -> list[Path]:
    """Sibling JSON candidates in priority order: the AF3-named file (when the
    stem matches `<base>_model_N`) first, then the stem-specific patterns."""
    parent, stem = path.parent, path.stem
    af3_base, af3_n = _af3_base_and_index(stem)
    candidates = []
    if af3_base is not None:
        candidates.append(parent / f"{af3_base}_{af3_suffix}_{af3_n}.json")
    candidates += [parent / pat.format(stem=stem) for pat in stem_patterns]
    return candidates


def get_pae_candidates(path: Path) -> list[Path]:
    """Candidate PAE JSON siblings for a structure, in priority order.

    AF3 Server names structures `<base>_model_N.cif` with sibling
    `<base>_full_data_N.json` — a different stem, so plain `{stem}_*` patterns
    miss it; we add the AF3 name explicitly when the stem matches.
    """
    return sibling_candidates(
        path,
        "full_data",
        ["{stem}_pae.json", "pae_{stem}.json", "{stem}_full_data_0.json"],
    )


def get_conf_candidates(path: Path) -> list[Path]:
    """Candidate confidence JSON siblings for a structure, in priority order.

    Only stem-specific siblings: bare-named confidence files (AF2's per-job
    `ranking_debug.json`, `summary_confidences.json`) carry no stem, so in a
    flat batch of unrelated predictions they would leak one job's ipTM/pTM onto
    every other structure. We require the stem to disambiguate ownership. AF3's
    `<base>_summary_confidences_N.json` is added explicitly when the stem matches.
    """
    return sibling_candidates(
        path,
        "summary_confidences",
        ["{stem}_summary_confidences.json", "{stem}_confidences.json"],
    )


def find_sibling_json(path: Path) -> dict:
    """Search for PAE/confidence JSON files alongside the structure.

    Returns a dict that may contain: pae, iptm, ptm, ranking_score.
    """
    extra: dict = {}

    for pae_path in get_pae_candidates(path):
        if not pae_path.exists():
            continue
        try:
            data = json.loads(pae_path.read_text())
        except json.JSONDecodeError:
            break
        # AF3 server uses "pae"; AF2-Multimer uses "predicted_aligned_error".
        container = (
            data[0]
            if isinstance(data, list) and data and isinstance(data[0], dict)
            else data
        )
        pae = container.get("pae") if isinstance(container, dict) else None
        if pae is None and isinstance(container, dict):
            pae = container.get("predicted_aligned_error")
        if pae is not None:
            with contextlib.suppress(ValueError):
                extra["pae"] = np.array(pae)
        break

    for conf_path in get_conf_candidates(path):
        if not conf_path.exists():
            continue
        try:
            data = json.loads(conf_path.read_text())
        except json.JSONDecodeError:
            break
        if not isinstance(data, dict):
            break
        # Extract each field independently so a null iptm (e.g. AF3 monomers)
        # doesn't poison ptm or ranking_score.
        iptm = data.get("iptm")
        if iptm is None:
            iptm = data.get("iptm+ptm")
        if isinstance(iptm, (int, float)):
            extra["iptm"] = float(iptm)
        ptm = data.get("ptm")
        if isinstance(ptm, (int, float)):
            extra["ptm"] = float(ptm)
        ranking = data.get("ranking_score")
        if isinstance(ranking, (int, float)):
            extra["ranking_score"] = float(ranking)
        break

    return extra


def _has_plddt_evidence(structure, metrics: dict) -> bool:
    """Require prediction metadata, never infer confidence from B-factor range."""
    method = dict(structure.info).get("_exptl.method", "").upper()
    if method and method != "THEORETICAL MODEL":
        return False
    if metrics.get("plddt_in_b_factors") or any(
        metrics.get(key) is not None for key in ("pae", "ptm", "iptm")
    ):
        return True
    # AlphaFold PDB exports describe the confidence/B-factor convention in
    # REMARKs; CIF exports may identify the prediction software instead.
    if any("plddt" in remark.lower() for remark in structure.raw_remarks):
        return True
    return any(
        "alphafold" in software.name.lower() or "colabfold" in software.name.lower()
        for software in structure.meta.software
    )


def extract_record(path: Path, name: str | None = None) -> StructureRecord:
    """Extract structure metadata using gemmi.

    Raises RuntimeError/ValueError if the file can't be parsed (gemmi) or has no
    models — callers decide whether to surface or skip it.
    """
    path = Path(path)
    if name is None:
        name = path.stem

    structure = gemmi.read_structure(str(path))
    if len(structure) == 0:
        raise ValueError(f"{path.name}: file has no models")
    model = structure[0]

    chain_order: list[str] = []
    plddt_vals: list[float] = []
    residue_ids = []
    for chain in model:
        if chain.name not in chain_order:
            chain_order.append(chain.name)
        for residue in chain:
            for atom in residue:
                if atom.name == "CA":
                    plddt_vals.append(atom.b_iso)
                    residue_ids.append((chain.name, residue.seqid.num, residue.seqid.icode.strip()))
                    break

    # CIF-embedded metrics take precedence over sibling JSON.
    merged = {**find_sibling_json(path), **metrics_from_cif(path)}
    plddt = None
    if plddt_vals and _has_plddt_evidence(structure, merged):
        arr = np.array(plddt_vals, dtype=np.float64)
        if np.isfinite(arr).all() and float(arr.min()) >= 0 and float(arr.max()) <= 100:
            plddt = arr

    return StructureRecord(
        name=name,
        path=path,
        chains=chain_order,
        n_residues=len(plddt_vals),
        plddt=plddt,
        pae=merged.get("pae"),
        iptm=merged.get("iptm"),
        ptm=merged.get("ptm"),
        ranking_score=merged.get("ranking_score"),
        residue_ids=residue_ids,
    )


def _consecutive(a: tuple[str, int, str], b: tuple[str, int, str]) -> bool:
    chain, number, insertion = a
    next_chain, next_number, next_insertion = b
    if chain != next_chain:
        return False
    if next_number == number + 1 and not next_insertion:
        return True
    return (next_number == number and len(next_insertion) == 1
            and ord(next_insertion) == ord(insertion or "@") + 1)


def find_low_confidence(record: StructureRecord, threshold: int = 70) -> str:
    """Find contiguous regions below pLDDT threshold."""
    if record.plddt is None:
        return f"{record.name}: no pLDDT data available"

    regions = []
    in_region = False
    start = 0
    ids = record.residue_ids
    if ids and len(ids) != len(record.plddt):
        raise ValueError("Residue identifiers do not match the pLDDT values")

    for i, val in enumerate(record.plddt):
        if in_region and ids and not _consecutive(ids[i - 1], ids[i]):
            regions.append((start, i - 1, float(np.mean(record.plddt[start:i]))))
            in_region = False
        if val < threshold:
            if not in_region:
                start = i
                in_region = True
        else:
            if in_region:
                regions.append((start, i - 1, float(np.mean(record.plddt[start:i]))))
                in_region = False

    if in_region:
        regions.append(
            (start, len(record.plddt) - 1, float(np.mean(record.plddt[start:])))
        )

    if not regions:
        return f"{record.name}: no regions below pLDDT {threshold}"

    lines = [
        f"{record.name}: {len(regions)} low-confidence regions (threshold={threshold}):"
    ]
    for start, end, mean_val in regions:
        length = end - start + 1
        if ids:
            chain, first, first_code = ids[start]
            _, last, last_code = ids[end]
            location = f"chain {chain or '(blank)'} residues {first}{first_code}-{last}{last_code}"
        else:
            location = f"positions {start + 1}-{end + 1} (residue identifiers unavailable)"
        lines.append(
            f"  {location} ({length} residues, mean pLDDT={mean_val:.1f})"
        )

    return "\n".join(lines)
