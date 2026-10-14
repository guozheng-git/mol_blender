/* Molecular parsing and scene construction. No DOM or host APIs. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.createStructureScene = factory;
})(globalThis, function createStructureScene(data) {
'use strict';
const { PALETTE, PLDDT, COVALENT_MIN_HEAVY, LIGAND_CARBON } = data;
const [WATER, ION_ELEMENTS, ADDITIVES, STANDARD, MODIFIED, NUCLEIC] =
  ['WATER', 'ION_ELEMENTS', 'ADDITIVES', 'STANDARD', 'MODIFIED', 'NUCLEIC'].map((key) => new Set(data[key]));
const DEFAULT_SCENE = { schema: 1, representation: 'cartoon', color: 'chain', chain_colors: {}, opacity: 1, hidden: [], ligands: true, ions: true, waters: false, layers: [], focus: null };
const toInt = (hex) => parseInt(hex.slice(1), 16);
const clone = (v) => JSON.parse(JSON.stringify(v));


let model = null, scene = normalize(), appliedFocus = null;
const siteCache = new Map();
function normalize(value) { return Object.assign(clone(DEFAULT_SCENE), value ? clone(value) : {}); }
function titleCase(s) { return s.length === 3 ? s[0] + s.slice(1).toLowerCase() : s; }
/* ---------- mmCIF / PDB parsing (first model, first altloc) ---------- */
const CIF_KEEP = new Set(['_atom_site', '_chem_comp', '_entity', '_entity_src_gen']);
const ATOM_COLS = ['group_PDB','type_symbol','label_atom_id','label_alt_id','label_comp_id','label_asym_id','label_entity_id','label_seq_id','pdbx_PDB_ins_code','Cartn_x','Cartn_y','Cartn_z','B_iso_or_equiv','auth_seq_id','auth_asym_id','auth_comp_id','auth_atom_id','pdbx_PDB_model_num'];
function parseCif(text) {
  const items = {}, loops = {};
  const lines = text.split(/\r?\n/);
  const re = /'(.*?)'(?=\s|$)|"(.*?)"(?=\s|$)|(\S+)/g;
  let mode = 0, tags = [], cols = null, keep = false, idx = 0, pending = null, blocks = 0, cat = '';
  const finish = () => { if (mode && keep && tags.length) loops[cat] = { tags, cols }; mode = 0; tags = []; cols = null; };
  const value = (v) => {
    if (pending) { items[pending] = v; pending = null; return; }
    if (mode === 1) {
      mode = 2; cat = tags[0].split('.')[0]; keep = CIF_KEEP.has(cat) || cat.startsWith('_ma_qa_metric');
      cols = tags.map((t) => (keep && (cat !== '_atom_site' || ATOM_COLS.includes(t.split('.')[1]))) ? [] : null); idx = 0;
    }
    if (mode === 2) { const c = cols[idx]; if (c) c.push(v); idx = (idx + 1) % tags.length; }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.charCodeAt(0) === 59) {
      const buf = [line.slice(1)];
      while (++i < lines.length && lines[i].charCodeAt(0) !== 59) buf.push(lines[i]);
      value(buf.join('\n').trim()); continue;
    }
    re.lastIndex = 0; let m;
    while ((m = re.exec(line))) {
      if (m[3] !== undefined) {
        const t = m[3], c0 = t.charCodeAt(0);
        if (c0 === 35) break;
        if (c0 === 95) { if (mode === 1) { tags.push(t); continue; } if (mode === 2) finish(); pending = t; continue; }
        if (t === 'loop_') { finish(); mode = 1; continue; }
        if (t.startsWith('data_')) { if (++blocks > 1) { finish(); return { items, loops }; } continue; }
        value(t);
      } else value(m[1] !== undefined ? m[1] : m[2]);
    }
  }
  finish();
  return { items, loops };
}
function loopRows(loop, field) { if (!loop) return null; const i = loop.tags.findIndex((t) => t.endsWith('.' + field)); return i < 0 ? null : loop.cols[i]; }
function newAtoms() {
  const a = { het: [], el: [], name: [], comp: [], labelAsym: [], labelSeq: [], authAsym: [], authSeq: [], ins: [], x: [], y: [], z: [], b: [], entity: [], length: 0 };
  a.push = (het, el, name, comp, labelAsym, labelSeq, authAsym, authSeq, ins, x, y, z, b, entity) => {
    a.het.push(het); a.el.push(el); a.name.push(name); a.comp.push(comp); a.labelAsym.push(labelAsym); a.labelSeq.push(labelSeq);
    a.authAsym.push(authAsym); a.authSeq.push(authSeq); a.ins.push(ins); a.x.push(x); a.y.push(y); a.z.push(z); a.b.push(b); a.entity.push(entity); a.length++;
  };
  return a;
}
function modelFromCif(text, filename) {
  const { items, loops } = parseCif(text);
  const L = loops._atom_site; if (!L) throw new Error('This file has no atom coordinates (_atom_site).');
  const col = (f) => loopRows(L, f), pick = (a, b) => col(a) || col(b);
  const n = col('Cartn_x').length, models = col('pdbx_PDB_model_num'), alt = col('label_alt_id'), A = newAtoms();
  const g = col('group_PDB'), el = col('type_symbol'), an = pick('auth_atom_id', 'label_atom_id'), comp = pick('auth_comp_id', 'label_comp_id');
  const la = col('label_asym_id'), ls = col('label_seq_id'), aa = pick('auth_asym_id', 'label_asym_id'), as = pick('auth_seq_id', 'label_seq_id'), ins = col('pdbx_PDB_ins_code'), ent = col('label_entity_id');
  const x = col('Cartn_x'), y = col('Cartn_y'), z = col('Cartn_z'), b = col('B_iso_or_equiv');
  const firstModel = models ? models[0] : null;
  for (let i = 0; i < n; i++) {
    if (models && models[i] !== firstModel) continue;
    const al = alt ? alt[i] : '.'; if (al !== '.' && al !== '?' && al !== 'A' && al !== '1') continue;
    const seq = ls ? ls[i] : '.';
    A.push(g ? g[i] === 'HETATM' : false, (el ? el[i] : an[i][0] || '').toUpperCase(), an[i], comp[i], la ? la[i] : aa[i], (seq === '.' || seq === '?') ? null : +seq,
      aa[i], parseInt(as[i], 10) || 0, ins && ins[i] !== '?' && ins[i] !== '.' ? ins[i] : '', +x[i], +y[i], +z[i], b ? +b[i] : 0, ent ? ent[i] : '');
  }
  const names = {}; const cc = loops._chem_comp;
  if (cc) { const ids = loopRows(cc, 'id'), nm = loopRows(cc, 'name'); if (ids && nm) ids.forEach((id, i) => { names[id] = nm[i]; }); }
  else if (items['_chem_comp.id']) names[items['_chem_comp.id']] = items['_chem_comp.name'];
  const entities = {}; const E = loops._entity;
  if (E) { const ids = loopRows(E, 'id'), d = loopRows(E, 'pdbx_description'); if (ids && d) ids.forEach((id, i) => { entities[id] = d[i]; }); }
  else if (items['_entity.id']) entities[items['_entity.id']] = items['_entity.pdbx_description'];
  const genes = {}; const G = loops._entity_src_gen;
  if (G) { const ids = loopRows(G, 'entity_id'), gn = loopRows(G, 'pdbx_gene_src_gene'); if (ids && gn) ids.forEach((id, i) => { genes[id] = gn[i]; }); }
  else if (items['_entity_src_gen.entity_id']) genes[items['_entity_src_gen.entity_id']] = items['_entity_src_gen.pdbx_gene_src_gene'];
  for (const k of Object.keys(genes)) { const v = genes[k]; genes[k] = v && v !== '?' && v !== '.' ? v.split(/[,;\s]+/)[0].toUpperCase() : ''; }
  const hasMaQa = Object.keys(loops).some((k) => k.startsWith('_ma_qa_metric')) || Object.keys(items).some((k) => k.startsWith('_ma_qa_metric'));
  const res = items['_refine.ls_d_res_high'] || items['_em_3d_reconstruction.resolution'] || items['_reflns.d_resolution_high'];
  return finishModel(A, { format: 'mmcif', filename, names, entities, genes, hasMaQa,
    info: { id: items['_entry.id'], title: items['_struct.title'], method: items['_exptl.method'], resolution: res && !isNaN(+res) ? +res : null } });
}
function modelFromPdb(text, filename) {
  const A = newAtoms(); const names = {}; let title = '', id = '', method = '', resolution = null;
  for (const line of text.split(/\r?\n/)) {
    const rec = line.slice(0, 6);
    if (rec === 'ENDMDL') break;
    if (rec === 'ATOM  ' || rec === 'HETATM') {
      const altl = line[16]; if (altl !== ' ' && altl !== 'A' && altl !== '1' && altl !== undefined) continue;
      const atomName = line.slice(12, 16).trim();
      let el = line.slice(76, 78).trim().toUpperCase(); if (!el) el = atomName.replace(/[^A-Za-z]/g, '').slice(0, 1).toUpperCase();
      const chain = line.slice(21, 22).trim();
      A.push(rec === 'HETATM', el, atomName, line.slice(17, 20).trim(), chain, null, chain, parseInt(line.slice(22, 26), 10) || 0,
        line[26] === ' ' ? '' : (line[26] || ''), +line.slice(30, 38), +line.slice(38, 46), +line.slice(46, 54), +line.slice(60, 66) || 0, '');
    } else if (rec === 'HEADER') id = line.slice(62, 66).trim();
    else if (rec === 'TITLE ') title += (title ? ' ' : '') + line.slice(10).trim();
    else if (rec === 'HETNAM') { const k = line.slice(11, 14).trim(); names[k] = ((names[k] ? names[k] + ' ' : '') + line.slice(15).trim()).trim(); }
    else if (rec === 'EXPDTA') method = line.slice(10).trim();
    else if (rec === 'REMARK' && line.slice(6, 10) === '   2' && /RESOLUTION\./.test(line)) { const r = parseFloat(line.slice(22)); if (!isNaN(r)) resolution = r; }
  }
  return finishModel(A, { format: 'pdb', filename, names, entities: {}, genes: {}, hasMaQa: false, info: { id, title, method, resolution } });
}
function finishModel(A, meta) {
  if (!A.length && meta.format !== 'bcif') throw new Error('No atoms were found in this file.');
  const residues = []; const atomRes = new Int32Array(A.length);
  let cur = null;
  for (let i = 0; i < A.length; i++) {
    if (!cur || A.authAsym[i] !== cur.authAsym || A.authSeq[i] !== cur.authSeq || A.ins[i] !== cur.ins || A.comp[i] !== cur.comp || A.labelAsym[i] !== cur.labelAsym) {
      cur = { index: residues.length, comp: A.comp[i], authAsym: A.authAsym[i], authSeq: A.authSeq[i], ins: A.ins[i], labelAsym: A.labelAsym[i], labelSeq: A.labelSeq[i], entity: A.entity[i], start: i, end: i + 1, het: A.het[i], atoms: new Set() };
      residues.push(cur);
    }
    cur.end = i + 1; cur.atoms.add(A.name[i]); atomRes[i] = residues.length - 1;
  }
  const chainOrder = []; const chains = new Map();
  for (const r of residues) {
    const backbone = (r.atoms.has('CA') && r.atoms.has('N') && r.atoms.has('C')) || (r.atoms.has('P') && r.atoms.has("C4'"));
    /* PyMOL-written mmCIF numbers ligands like chain residues, so HETATM residues also need a backbone. */
    r.polymer = (meta.format === 'mmcif' ? r.labelSeq !== null : true) && (!r.het || backbone);
    let heavy = 0; for (let i = r.start; i < r.end; i++) if (A.el[i] !== 'H' && A.el[i] !== 'D') heavy++;
    r.heavy = heavy;
    r.covalent = r.polymer && !STANDARD.has(r.comp) && !MODIFIED.has(r.comp) && heavy >= COVALENT_MIN_HEAVY;
    if (WATER.has(r.comp)) r.kind = 'water';
    else if (r.covalent) r.kind = 'ligand';
    else if (r.polymer) r.kind = 'polymer';
    else if (r.end - r.start === 1 && ION_ELEMENTS.has(A.el[r.start])) r.kind = 'ion';
    else r.kind = 'ligand';
    if (!chains.has(r.authAsym)) { chains.set(r.authAsym, { id: r.authAsym, residues: 0, nucleic: 0, entity: '' }); chainOrder.push(r.authAsym); }
    if (r.kind === 'polymer' || r.covalent) { const c = chains.get(r.authAsym); c.residues++; if (NUCLEIC.has(r.comp)) c.nucleic++; if (!c.entity) c.entity = r.entity; }
    delete r.atoms;
  }
  const chainList = chainOrder.map((id) => chains.get(id)).filter((c) => c.residues).map((c, i) => ({
    id: c.id, index: i, residues: c.residues, kind: c.nucleic > c.residues / 2 ? 'nucleic' : 'protein',
    description: meta.entities[c.entity] || '', gene: (meta.genes || {})[c.entity] || '' }));
  const ligands = residues.filter((r) => r.kind === 'ligand').map((r) => Object.assign(r, {
    key: `${r.comp} ${r.authAsym || ':'}${r.authSeq}${r.ins}`, additive: ADDITIVES.has(r.comp), fullName: (meta.names[r.comp] || '') + (r.covalent ? ' (covalent)' : '') }))
    .sort((p, q) => (p.additive - q.additive) || (q.heavy - p.heavy));
  const m = Object.assign({ atoms: A, residues, atomRes, chains: chainList, ligands }, meta);
  m.waters = residues.filter((r) => r.kind === 'water').length;
  m.ions = residues.filter((r) => r.kind === 'ion').length;
  const hint = `${meta.filename} ${meta.info.title || ''}`;
  m.predicted = meta.hasMaQa || /(^|[\/_\s-])(AF-|fold_|alphafold|af2|af3|esmfold|boltz|chai)/i.test(hint) || /predicted|alphafold/i.test(meta.info.title || '');
  return m;
}

/* ---------- Selections (the scene vocabulary) ---------- */
function site(lig, cutoff) {
  const key = lig.key + '@' + cutoff;
  if (siteCache.has(key)) return siteCache.get(key);
  const A = model.atoms, c2 = cutoff * cutoff, idx = [];
  let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
  for (let i = lig.start; i < lig.end; i++) {
    if (A.el[i] === 'H') continue; idx.push(i);
    x0 = Math.min(x0, A.x[i]); y0 = Math.min(y0, A.y[i]); z0 = Math.min(z0, A.z[i]);
    x1 = Math.max(x1, A.x[i]); y1 = Math.max(y1, A.y[i]); z1 = Math.max(z1, A.z[i]);
  }
  const hits = new Set(), waters = new Set();
  for (let j = 0; j < A.length; j++) {
    const x = A.x[j], y = A.y[j], z = A.z[j];
    if (x < x0 - cutoff || x > x1 + cutoff || y < y0 - cutoff || y > y1 + cutoff || z < z0 - cutoff || z > z1 + cutoff) continue;
    const r = model.atomRes[j]; if (r === lig.index || A.el[j] === 'H') continue;
    for (const i of idx) {
      const dx = A.x[i] - x, dy = A.y[i] - y, dz = A.z[i] - z;
      if (dx * dx + dy * dy + dz * dz <= c2) { (model.residues[r].kind === 'water' ? waters : hits).add(r); break; }
    }
  }
  const out = { residues: [...hits].sort((a, b) => a - b), waters: [...waters] };
  siteCache.set(key, out);
  return out;
}
function matchLigands(text) {
  const w = String(text || '').trim().toUpperCase().replace(/\s+/g, ' ');
  if (!w || !model) return [];
  const exact = model.ligands.filter((l) => l.key.toUpperCase() === w);
  return exact.length ? exact : model.ligands.filter((l) => l.comp.toUpperCase() === w);
}
function parseRanges(text) {
  return String(text).split(',').map((item) => {
    const m = /^(-?\d+)([A-Za-z]?)(?:-(-?\d+)([A-Za-z]?))?$/.exec(item.trim());
    return m ? { lo: +m[1], loIns: m[2] || '', hi: m[3] !== undefined ? +m[3] : null } : null;
  }).filter(Boolean);
}
function kindMatch(r, kind) {
  switch (kind) {
    case 'polymer': return r.kind === 'polymer';
    case 'protein': return r.kind === 'polymer' && !NUCLEIC.has(r.comp);
    case 'nucleic': return r.kind === 'polymer' && NUCLEIC.has(r.comp);
    case 'ligand': case 'water': case 'ion': return r.kind === kind;
    default: return true;
  }
}
/** Residue indices for one scene selection; its fields combine with AND. */
function resolve(sel) {
  if (!sel || !model) return [];
  const R = model.residues;
  let idx = R.map((r) => r.index);
  if (sel.kind && sel.kind !== 'all') idx = idx.filter((i) => kindMatch(R[i], sel.kind));
  if (sel.chain) { const set = new Set([].concat(sel.chain)); idx = idx.filter((i) => set.has(R[i].authAsym)); }
  if (sel.residues !== undefined) {
    const ranges = parseRanges(sel.residues);
    idx = idx.filter((i) => ranges.some((g) => g.hi === null ? (R[i].authSeq === g.lo && (!g.loIns || R[i].ins === g.loIns)) : (R[i].authSeq >= g.lo && R[i].authSeq <= g.hi)));
  }
  if (sel.ligand) { const set = new Set(matchLigands(sel.ligand).map((l) => l.index)); idx = idx.filter((i) => set.has(i)); }
  if (sel.site) {
    const lig = matchLigands(sel.site)[0];
    const set = new Set(lig ? site(lig, sel.cutoff || 5).residues : []);
    idx = idx.filter((i) => set.has(i));
  }
  return idx;
}
function selector(r) {
  const s = {};
  if (model.format === 'mmcif') { s.label_asym_id = r.labelAsym; if (r.labelSeq !== null) s.label_seq_id = r.labelSeq; else s.auth_seq_id = r.authSeq; }
  else { s.auth_asym_id = r.authAsym; s.auth_seq_id = r.authSeq; }
  if (r.ins) s.pdbx_PDB_ins_code = r.ins;
  return s;
}
/** Compact MolViewSpec selectors: consecutive polymer residues become one range. */
function selectors(indices) {
  const R = model.residues, out = []; let run = null;
  const flush = () => { if (run) { out.push(run.beg === run.end ? run.single : run.range()); run = null; } };
  for (const i of [...indices].sort((a, b) => a - b)) {
    const r = R[i];
    const ranged = r.polymer && !r.ins && (model.format === 'mmcif' ? r.labelSeq !== null : true);
    if (ranged && run && run.asym === (model.format === 'mmcif' ? r.labelAsym : r.authAsym) && i === run.last + 1) {
      run.end = model.format === 'mmcif' ? r.labelSeq : r.authSeq; run.last = i; continue;
    }
    flush();
    if (!ranged) { out.push(selector(r)); continue; }
    const mm = model.format === 'mmcif', asym = mm ? r.labelAsym : r.authAsym, pos = mm ? r.labelSeq : r.authSeq;
    run = { asym, beg: pos, end: pos, last: i, single: selector(r),
      range() { return mm ? { label_asym_id: this.asym, beg_label_seq_id: this.beg, end_label_seq_id: this.end } : { auth_asym_id: this.asym, beg_auth_seq_id: this.beg, end_auth_seq_id: this.end }; } };
  }
  flush();
  return out;
}

/* ---------- Scene → MolViewSpec ---------- */
const N = (kind, params, children, custom) => { const n = { kind, params: params || {} }; if (children && children.length) n.children = children; if (custom) n.custom = custom; return n; };
const theme = (name, params) => N('color', { color: '#cccccc' }, null, params ? { molstar_color_theme_name: name, molstar_color_theme_params: params } : { molstar_color_theme_name: name });
const HYDROGEN_FREE = new Set(['ball_and_stick', 'spacefill', 'surface']);
function rep(type, params, coloring, extra) {
  const base = HYDROGEN_FREE.has(type) ? { type, ignore_hydrogens: true } : { type };
  return N('representation', Object.assign(base, params || {}), [coloring, ...(extra || [])]);
}
function chainColor(id) {
  if (scene.chain_colors && scene.chain_colors[id]) return scene.chain_colors[id];
  const c = model.chains.find((x) => x.id === id);
  return PALETTE[(c ? c.index : 0) % PALETTE.length];
}
function elementTheme(carbon) { return theme('element-symbol', { carbonColor: { name: 'uniform', params: { value: toInt(carbon) } } }); }
function coloring(color, chain) {
  if (/^#[0-9a-f]{6}$/i.test(color)) return N('color', { color });
  switch (color) {
    case 'ss': return theme('secondary-structure');
    case 'rainbow': return theme('sequence-id');
    case 'element': return elementTheme(chainColor(chain));
    case 'plddt': case 'bfactor':
      if (!model.predicted && color === 'bfactor') return theme('uncertainty');
      if (model.hasMaQa) return theme('plddt-confidence');
      return N('color_from_uri', { uri: 'plddt.json', format: 'json', schema: 'residue', field_name: 'color' });
    default: return N('color', { color: chainColor(chain) });
  }
}
function byChain(indices) {
  const groups = new Map();
  for (const i of indices) { const c = model.residues[i].authAsym; if (!groups.has(c)) groups.set(c, []); groups.get(c).push(i); }
  return groups;
}
const REPR = { cartoon: 'cartoon', surface: 'surface', spacefill: 'spacefill', putty: 'putty', backbone: 'backbone', sticks: 'ball_and_stick' };
function buildScene(background = '#ffffff') {
  const R = model.residues, comps = [];
  const hidden = new Set();
  for (const h of scene.hidden) for (const i of resolve(h)) hidden.add(i);
  for (const [flag, kind] of [['ligands', 'ligand'], ['ions', 'ion'], ['waters', 'water']]) if (!scene[flag]) for (const r of R) if (r.kind === kind) hidden.add(r.index);
  const visible = (kind) => R.filter((r) => r.kind === kind && !hidden.has(r.index)).map((r) => r.index);
  if (scene.representation !== 'hidden') {
    const params = scene.representation === 'surface' ? { surface_type: 'molecular' } : {};
    for (const [chain, idx] of byChain(visible('polymer'))) {
      const extra = scene.opacity < 1 ? [N('opacity', { opacity: scene.opacity })] : [];
      comps.push(N('component', { selector: selectors(idx) }, [rep(REPR[scene.representation], params, coloring(scene.color, chain), extra)]));
    }
  }
  const ligandType = scene.representation === 'spacefill' ? 'spacefill' : 'ball_and_stick';
  const ligs = visible('ligand');
  if (ligs.length) comps.push(N('component', { selector: selectors(ligs) }, [rep(ligandType, {}, elementTheme(LIGAND_CARBON))]));
  const ions = visible('ion');
  if (ions.length) comps.push(N('component', { selector: selectors(ions) }, [rep('spacefill', { size_factor: scene.representation === 'spacefill' ? 1 : 0.6 }, theme('element-symbol'))]));
  const waters = visible('water');
  if (waters.length) comps.push(N('component', { selector: selectors(waters) }, [rep('ball_and_stick', { size_factor: 0.6 }, theme('element-symbol'))]));
  let labels = 0;
  for (const layer of scene.layers) {
    const idx = resolve(layer.select).filter((i) => !hidden.has(i));
    const type = REPR[layer.representation || 'sticks'];
    const multi = new Set(idx.map((i) => R[i].authAsym)).size > 1;
    let first = true;
    for (const [chain, part] of byChain(idx)) {
      const color = (layer.color || 'element') === 'element'
        ? elementTheme(R[part[0]].kind === 'ligand' ? LIGAND_CARBON : chainColor(chain)) : coloring(layer.color, chain);
      const extra = layer.opacity !== undefined && layer.opacity < 1 ? [N('opacity', { opacity: layer.opacity })] : [];
      const children = [rep(type, type === 'surface' ? { surface_type: 'molecular' } : {}, color, extra)];
      if (typeof layer.label === 'string' && first) children.push(N('label', { text: layer.label }));
      comps.push(N('component', { selector: selectors(part) }, children));
      first = false;
    }
    if (layer.label === true) {
      for (const i of idx) {
        if (labels >= 40) break;
        const r = R[i];
        const text = r.kind === 'ligand' ? r.key : `${titleCase(r.comp)}${r.authSeq}${r.ins}${multi ? ' ' + r.authAsym : ''}`;
        comps.push(N('component', { selector: selector(r) }, [N('label', { text })])); labels++;
      }
    }
    if (layer.select.site || layer.select.ligand) {
      for (const i of idx) { const r = R[i]; if (r.kind === 'polymer') comps.push(N('component', { selector: selector(r) }, [N('tooltip', { text: `${titleCase(r.comp)}${r.authSeq}${r.ins} ${r.authAsym}` })])); }
    }
  }
  const focusKey = scene.focus ? JSON.stringify(scene.focus) : null;
  let refocus = false;
  if (focusKey && focusKey !== appliedFocus) {
    const idx = resolve(scene.focus);
    const withSite = scene.focus.ligand ? idx.concat(...matchLigands(scene.focus.ligand).slice(0, 1).map((l) => site(l, 5).residues)) : idx;
    if (withSite.length) { comps.push(N('component', { selector: selectors(withSite) }, [N('focus', {})])); refocus = true; }
  }
  appliedFocus = focusKey;
  const tree = { root: N('root', {}, [
    N('canvas', { background_color: background }),
    N('download', { url: model.archiveName }, [N('parse', { format: model.format }, [N('structure', { type: 'model' }, comps)])]),
  ]), metadata: { version: '1', timestamp: new Date().toISOString(), title: model.info.id || model.filename } };
  return { tree, refocus };
}
function plddtAnnotation() {
  const rows = [];
  for (const r of model.residues) {
    if (r.kind !== 'polymer') continue;
    let b = model.atoms.b[r.start];
    for (let i = r.start; i < r.end; i++) if (model.atoms.name[i] === 'CA' || model.atoms.name[i] === "C4'") { b = model.atoms.b[i]; break; }
    const row = selector(r); row.color = PLDDT.find((p) => b > p[0])[1]; rows.push(row);
  }
  return rows;
}


function loadModel(bytes, filename, predicted) {
  const ext = filename.split('.').pop().toLowerCase();
  if (!['cif', 'mmcif', 'pdb', 'ent'].includes(ext)) {
    throw new Error('This viewer needs mmCIF or PDB text; convert BinaryCIF to mmCIF first.');
  }
  const text = new TextDecoder().decode(bytes);
  const m = ext === 'pdb' || ext === 'ent' ? modelFromPdb(text, filename) : modelFromCif(text, filename);
  if (predicted != null) m.predicted = !!predicted;
  m.bytes = bytes;
  m.archiveName = 'structure.' + (m.format === 'mmcif' ? 'cif' : m.format);
  model = m;
  siteCache.clear();
  appliedFocus = null;
  return model;
}
function setScene(value) { scene = normalize(value); return scene; }
function references(node, uri) {
  return (node.kind === 'color_from_uri' && node.params.uri === uri) || (node.children || []).some((child) => references(child, uri));
}
function sceneFiles(background) {
  const { tree, refocus } = buildScene(background);
  const encode = (value) => new TextEncoder().encode(JSON.stringify(value));
  const files = [['index.mvsj', encode(tree)], [model.archiveName, model.bytes]];
  if (references(tree.root, 'plddt.json')) files.push(['plddt.json', encode(plddtAnnotation())]);
  return { files, refocus };
}
function showChain(hidden, chain) {
  return hidden.flatMap((selection) => {
    if (!Object.hasOwn(selection, 'chain') || Object.keys(selection).length !== 1) return [clone(selection)];
    const remaining = [].concat(selection.chain).filter((id) => id !== chain);
    return remaining.length ? [{ chain: remaining }] : [];
  });
}
return { loadModel, setScene, normalize, resolve, buildScene, sceneFiles, matchLigands, site, chainColor, showChain, titleCase };
});
