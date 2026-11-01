(() => {
'use strict';
const DATA = JSON.parse(document.getElementById('studio-data').textContent);
const $ = (s) => document.querySelector(s);

const engine = createStructureScene(DATA.molecular_data);
const { normalize, resolve, buildScene, sceneFiles, matchLigands, site, chainColor, showChain, titleCase } = engine;
const { b64ToBytes, gunzip, sha256, zipStore, decodeAsset, readResponse, parseBundles, decodeBundle, LIMITS } = StructureStudioFiles;
const { PLDDT } = DATA.molecular_data;
const clone = (value) => JSON.parse(JSON.stringify(value));
const toInt = (hex) => parseInt(hex.slice(1), 16);

/* ---------- State ---------- */
let model = null, viewer = null, loading = Promise.resolve(), spinning = false;
let scene = engine.setScene(DATA.scene);
const sync = { mode: 'static', db: null, canWrite: null, writes: Promise.resolve(), lastCameraRequest: null, renders: null, meta: null };

/* ---------- Utilities ---------- */
function core(s) { const c = clone(s); for (const k of ['note', 'revision', 'updated_by', 'written_at']) delete c[k]; return JSON.stringify(c); }
function toast(text) { const t = $('#toast'); t.textContent = text; t.hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 2600); }
function status(text, isError) { const s = $('#status'); s.textContent = text || ''; s.classList.toggle('error', !!isError); }
function fmtBytes(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n >= 1024 ? Math.round(n / 1024) + ' KB' : n + ' B'; }
function ago(iso) { const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000); return s < 90 ? Math.round(s) + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : new Date(iso).toLocaleString(); }
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
const use = (name) => (window.claude && typeof window.claude.use === 'function') ? window.claude.use(name).catch(() => null) : Promise.resolve(null);
let downloadsApi;
function downloads() { if (!downloadsApi) downloadsApi = use('downloads'); return downloadsApi; }
async function saveFile(filename, data) {
  const api = await downloads();
  if (!api) { toast('Saving files works when this page is opened on claude.ai.'); return; }
  try { await api.save({ filename, data }); }
  catch (e) { if (e && e.code === 'declined') return; toast(e && e.code === 'too_large' ? 'That file is too large to save from this page.' : 'Saving is not available in this view.'); }
}

/* ---------- Viewer ---------- */
function pluginConfig() {
  const C = molstar.lib && molstar.lib.plugin && molstar.lib.plugin.PluginConfig;
  return C ? [[C.Viewport.ShowIllumination, false], [C.Viewport.ShowXR, 'never'], [C.Viewport.ShowTrajectoryControls, false], [C.Viewport.ShowAnimation, false]] : [];
}
async function createViewer() {
  if (!window.molstar || !molstar.Viewer) throw new Error('The 3D viewer could not load Mol* from cdn.jsdelivr.net.');
  viewer = await molstar.Viewer.create('molstar', {
    layoutIsExpanded: false, layoutShowControls: false, layoutShowRemoteState: false, layoutShowSequence: true,
    layoutShowLog: false, layoutShowLeftPanel: true, collapseLeftPanel: true,
    viewportShowExpand: false, viewportShowSelectionMode: false, viewportShowAnimation: false,
    viewportShowTrajectoryControls: false, viewportShowScreenshotControls: false, viewportShowToggleFullscreen: false,
    viewportShowSettings: false, viewportShowReset: false, viewportShowControls: false, config: pluginConfig(),
    disabledExtensions: ['rcsb-assembly-symmetry', 'rcsb-validation-report', 'pdbe-structure-quality-report', 'anvil-membrane-orientation', 'zenodo-import', 'sb-ncbr-partial-charges'],
    pdbProvider: 'pdbe', emdbProvider: 'pdbe', volumeStreamingDisabled: true,
  });
  new ResizeObserver(() => viewer && viewer.handleResize()).observe($('#stage'));
  watchCamera(); watchClicks();
}
function applyBackground() { const c = viewer && viewer.plugin.canvas3d; if (c) c.setProps({ renderer: { backgroundColor: toInt(cssVar('--canvas') || '#ffffff') } }); }
function setCamera(cam, ms) {
  /* Mol* rescales the distance when the field of view changes, so set fov first and the pose second. */
  const c = viewer.plugin.canvas3d.camera;
  c.setState({ fov: (cam.fov_deg || 45) * Math.PI / 180, mode: cam.mode || 'perspective' }, 0);
  c.setState({ position: cam.position, target: cam.target, up: cam.up || [0, 1, 0] }, ms || 0);
}
function draw(opts) {
  const run = async () => {
    if (!viewer || !model) return;
    const camera = opts && opts.camera;
    const { files, refocus } = sceneFiles(cssVar('--canvas') || '#ffffff');
    status(scene.representation === 'surface' ? 'Computing surface…' : 'Drawing…');
    try {
      /* With an exact camera, Mol* must not animate its own framing afterwards (it would win the race). */
      await viewer.loadMvsData(zipStore(files), 'mvsx', { replaceExisting: true, keepCamera: camera ? true : !(opts && opts.fresh) && !refocus });
      if (camera) await settleCamera(camera);
      const snaps = viewer.plugin.managers.snapshot; if (snaps && snaps.clear) snaps.clear();
      status('');
    } catch (e) { console.error(e); status('Mol* could not draw this scene: ' + (e && e.message || e), true); }
    syncControls();
  };
  loading = loading.then(run, run);
  return loading;
}
/* Mol* re-fits the camera once when a new structure first draws; apply an exact camera after that. */
async function settleCamera(cam) {
  setCamera(cam, 0);
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  setCamera(cam, 0);
  await new Promise((r) => setTimeout(r, 300));
  setCamera(cam, 0);
}
function cameraNow() {
  const s = viewer.plugin.canvas3d.camera.getSnapshot(); const arr = (v) => Array.from(v, (n) => +n.toFixed(3));
  return { position: arr(s.position), target: arr(s.target), up: arr(s.up), fov_deg: +(s.fov * 180 / Math.PI).toFixed(2), mode: s.mode };
}

/* ---------- Live workspace sync ---------- */
function setSync(mode, text) {
  sync.mode = mode;
  const el = $('#sync'); el.dataset.mode = mode;
  $('#sync-text').textContent = text || { live: 'Live: changes sync with chat', local: 'View only: your changes stay on this screen', static: 'Offline copy' }[mode];
}
async function connect() {
  const db = await use('db');
  if (!db || !DATA.workspace) { setSync('static'); return; }
  sync.db = db;
  const user = await use('user');
  sync.canWrite = user ? await user.can('data.write') : null;
  setSync(sync.canWrite === false ? 'local' : 'live');
  $('#btn-open').hidden = true;
  let first = true;
  db.doc('scene/current').onSnapshot((snap) => {
    if (!snap.exists) { first = false; return; }
    const data = snap.data();
    if (core(data) === core(scene)) { first = false; return; }
    scene = engine.setScene(data);
    draw();
    if (!first && data.updated_by !== 'page') toast('Updated from chat' + (data.note ? ': ' + data.note : ''));
    first = false;
  }, (e) => setSync('static', 'Live sync stopped (' + e.code + ')'));
  let firstCamera = true;
  db.doc('scene/camera').onSnapshot((snap) => {
    sync.cameraKnown = snap.exists;
    if (!snap.exists) { firstCamera = false; return; }
    const cam = snap.data();
    /* Open where the workspace was left (any author); afterwards follow only Claude's camera requests. */
    if (firstCamera) { firstCamera = false; sync.lastCameraRequest = cam.request_id || null; if (viewer && sync.drawn) setCamera(cam, 0); else sync.pendingCamera = cam; return; }
    if (cam.by === 'page' || !cam.request_id || cam.request_id === sync.lastCameraRequest) return;
    sync.lastCameraRequest = cam.request_id;
    if (viewer && sync.drawn) setCamera(cam, 600); else sync.pendingCamera = cam;
  });
  db.collection('renders').orderBy('created_at', 'desc').limit(200).onSnapshot((snap) => {
    sync.renders = snap.docs.map((d) => Object.assign({ id: d.id }, d.data()));
    buildGallery(); buildFiles();
  }, (e) => toast('Render list unavailable (' + e.code + ')'));
  db.doc('workspace/meta').get().then((snap) => { if (snap.exists) { sync.meta = snap.data(); buildFiles(); } }).catch(() => {});
}
function writeDoc(path, data) {
  if (!sync.db || sync.canWrite === false) return;
  sync.writes = sync.writes.then(() => sync.db.doc(path).set(data)).catch((e) => {
    if (e && e.code === 'invalid_argument') { sync.canWrite = false; setSync('local'); }
    else toast('Could not save to the workspace (' + (e && e.code || e) + ')');
  });
}
/** A control changed the scene: draw it now and share it when allowed. */
function changeScene(patch, note) {
  scene = engine.setScene(Object.assign(normalize(scene), patch, { revision: (scene.revision || 0) + 1, updated_by: 'page', note, written_at: new Date().toISOString() }));
  draw();
  writeDoc('scene/current', scene);
}
/* Save the camera only after someone moves the view, never because the page framed itself. */
let cameraTouched = false;
function touchCamera() { cameraTouched = true; }
function watchCamera() {
  let timer = null, last = '';
  for (const type of ['pointerdown', 'wheel', 'touchstart', 'keydown']) $('#stage').addEventListener(type, touchCamera, { passive: true });
  viewer.plugin.canvas3d.camera.stateChanged.subscribe(() => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (sync.mode !== 'live' || !cameraTouched) return;
      cameraTouched = false;
      const cam = cameraNow(); const key = JSON.stringify([cam.position, cam.target, cam.up].map((v) => v.map((n) => Math.round(n * 10))));
      if (key === last) return; last = key;
      writeDoc('scene/camera', Object.assign(cam, { by: 'page', saved_at: new Date().toISOString() }));
    }, 1200);
  });
}
function watchClicks() {
  const S = molstar.lib && molstar.lib.structure; if (!S || !S.StructureElement) return;
  const SE = S.StructureElement, SP = S.StructureProperties;
  viewer.plugin.behaviors.interaction.click.subscribe(({ current }) => {
    const loci = current && current.loci;
    if (!loci || !SE.Loci.is(loci) || SE.Loci.isEmpty(loci)) return;
    const loc = SE.Loci.getFirstLocation(loci);
    const item = { chain: SP.chain.auth_asym_id(loc), resi: SP.residue.auth_seq_id(loc), ins: SP.residue.pdbx_PDB_ins_code(loc) || '',
      comp: SP.atom.label_comp_id(loc), atom: SP.atom.label_atom_id(loc) };
    showPicked(item);
    writeDoc('scene/selection', { items: [item], by: 'page', saved_at: new Date().toISOString() });
  });
}
let picked = null;
function showPicked(item) { picked = item; renderBelow(); }

/* ---------- Controls ---------- */
function activeStyle() { return scene.layers.some((l) => l.select && l.select.site) ? 'site' : scene.representation; }
function syncControls() {
  const style = activeStyle();
  document.querySelectorAll('#seg-style .chip').forEach((b) => {
    b.setAttribute('aria-pressed', String(b.dataset.style === style));
    if (b.dataset.style === 'site') b.disabled = !model || !model.ligands.length;
  });
  document.querySelectorAll('#seg-color .chip').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.color === scene.color || (b.dataset.color === 'bfactor' && scene.color === 'plddt'))));
  $('#chip-b').textContent = model && model.predicted ? 'pLDDT' : 'B-factor';
  $('#water').checked = !!scene.waters; $('#water').disabled = !model || !model.waters;
  const siteLayer = scene.layers.find((l) => l.select && l.select.site);
  const lig = siteLayer ? matchLigands(siteLayer.select.site)[0] : null;
  if (lig) $('#ligand').value = lig.key;
  buildChains(); renderBelow();
}
function hiddenChains() { return new Set(scene.hidden.filter((h) => Object.hasOwn(h, 'chain') && Object.keys(h).length === 1).flatMap((h) => [].concat(h.chain))); }
function buildChains() {
  const box = $('#chains'); box.replaceChildren(); if (!model) return;
  const hidden = hiddenChains();
  for (const c of model.chains.slice(0, 40)) {
    const b = document.createElement('button'); b.className = 'chain'; b.setAttribute('aria-pressed', String(!hidden.has(c.id)));
    b.title = `${hidden.has(c.id) ? 'Show' : 'Hide'} chain ${c.id}${c.description ? ' (' + c.description + ')' : ''}`;
    const sw = document.createElement('span'); sw.className = 'swatch'; sw.style.background = chainColor(c.id);
    const id = document.createElement('span'); id.className = 'id'; id.textContent = c.id || '(blank)';
    const sub = document.createElement('span'); sub.className = 'sub'; sub.textContent = c.gene || (c.kind === 'nucleic' ? 'NA' : '') || `${c.residues} res`;
    b.append(sw, id, sub);
    b.addEventListener('click', () => {
      const next = showChain(scene.hidden, c.id);
      const hide = !hidden.has(c.id);
      if (hide) next.push({ chain: c.id });
      changeScene({ hidden: next }, `${hide ? 'hide' : 'show'} chain ${c.id}`);
    });
    box.append(b);
  }
}
function renderBelow() {
  const box = $('#below'); box.replaceChildren(); if (!model) return;
  const add = (text, mono) => { const s = document.createElement('span'); if (mono) s.className = 'mono'; s.textContent = text; box.append(s); };
  if (scene.color === 'bfactor' || scene.color === 'plddt') {
    if (model.predicted) for (const p of PLDDT) { const s = document.createElement('span'); s.innerHTML = '<span style="display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px"></span>'; s.firstChild.style.background = p[1]; s.append(p[2]); box.append(s); }
    else add('Blue → red: low → high B-factor (mobility)');
  } else if (scene.color === 'ss') add('Colored by secondary structure');
  else if (scene.color === 'rainbow') add('Rainbow from N- to C-terminus');
  const siteLayer = scene.layers.find((l) => l.select && l.select.site);
  if (siteLayer) { const lig = matchLigands(siteLayer.select.site)[0]; if (lig) add(`${lig.key}: ${site(lig, siteLayer.select.cutoff || 5).residues.filter((i) => model.residues[i].kind === 'polymer').length} residues within ${siteLayer.select.cutoff || 5} Å`, true); }
  if (picked) add(`Selected ${titleCase(picked.comp)}${picked.resi}${picked.ins} · chain ${picked.chain}${sync.mode === 'live' ? ' (Claude can see this)' : ''}`, true);
}
function populateHeader() {
  const i = model.info;
  $('#entry-id').textContent = (DATA.id || i.id || model.filename.replace(/\.(cif|mmcif|pdb|ent|bcif)(\.gz)?$/i, '')).toUpperCase();
  const described = model.chains.find((c) => c.description);
  $('#title').textContent = DATA.title || i.title || (described && described.description) || model.filename;
  const meta = $('#meta'); meta.replaceChildren();
  const add = (t) => { if (!t) return; const s = document.createElement('span'); s.textContent = t; meta.append(s); };
  add(model.predicted ? 'Predicted model' : (i.method ? i.method.replace(/\b\w+/g, (w) => w[0] + w.slice(1).toLowerCase()) : ''));
  add(i.resolution ? i.resolution.toFixed(2) + ' Å' : '');
  add(`${model.chains.length} chain${model.chains.length === 1 ? '' : 's'}`);
  add(`${model.atoms.length.toLocaleString()} atoms`);
  add(model.ligands.length ? `${model.ligands.length} ligand${model.ligands.length === 1 ? '' : 's'}` : 'No ligands');
  if (DATA.source) add(DATA.source);
}
function populateLigands() {
  const s = $('#ligand'); s.replaceChildren();
  if (!model.ligands.length) { const o = document.createElement('option'); o.textContent = 'None in this structure'; s.append(o); s.disabled = true; return; }
  s.disabled = false;
  for (const l of model.ligands.slice(0, 200)) {
    const o = document.createElement('option'); o.value = l.key;
    o.textContent = `${l.key}${l.fullName ? ' · ' + l.fullName.slice(0, 40).toLowerCase() : ''}${l.additive ? ' (additive)' : ''}`;
    s.append(o);
  }
}
function siteScene(key) {
  const layers = scene.layers.filter((l) => !(l.select && l.select.site));
  layers.push({ select: { site: key }, representation: 'sticks', color: 'element' });
  return { layers, focus: { ligand: key }, opacity: 0.45, representation: 'cartoon' };
}
function wire() {
  for (const t of ['3d', 'renders', 'files']) $('#tab-' + t).addEventListener('click', () => setTab(t));
  $('#seg-style').addEventListener('click', (e) => {
    const b = e.target.closest('.chip'); if (!b || b.disabled) return;
    if (b.dataset.style === 'site') { const key = $('#ligand').value || (model.ligands[0] && model.ligands[0].key); if (key) { touchCamera(); changeScene(siteScene(key), 'binding site of ' + key); } return; }
    changeScene({ representation: b.dataset.style, opacity: 1, layers: scene.layers.filter((l) => !(l.select && l.select.site)), focus: null }, b.dataset.style + ' style');
  });
  $('#seg-color').addEventListener('click', (e) => { const b = e.target.closest('.chip'); if (!b) return; const color = b.dataset.color === 'bfactor' && model && model.predicted ? 'plddt' : b.dataset.color; changeScene({ color }, 'color by ' + color); });
  $('#ligand').addEventListener('change', (e) => { touchCamera(); changeScene(siteScene(e.target.value), 'binding site of ' + e.target.value); });
  $('#water').addEventListener('change', (e) => changeScene({ waters: e.target.checked }, e.target.checked ? 'show waters' : 'hide waters'));
  $('#btn-reset').addEventListener('click', () => { if (!viewer) return; touchCamera(); viewer.plugin.managers.camera.reset(); });
  $('#btn-spin').addEventListener('click', (e) => {
    if (!viewer) return; spinning = !spinning; e.currentTarget.setAttribute('aria-pressed', String(spinning));
    viewer.plugin.canvas3d.setProps({ trackball: { animate: spinning ? { name: 'spin', params: { speed: 0.5 } } : { name: 'off', params: {} } } });
  });
  $('#btn-shot').addEventListener('click', async () => {
    if (!viewer) return;
    try { const uri = await viewer.plugin.helpers.viewportScreenshot.getImageDataUri(); await saveFile(`${($('#entry-id').textContent || 'structure').toLowerCase()}-view.png`, new Blob([b64ToBytes(uri.split(',')[1])], { type: 'image/png' })); }
    catch (err) { console.error(err); toast('Could not capture the viewport.'); }
  });
  $('#btn-view').addEventListener('click', () => {
    if (!viewer || !model) return;
    const v = Object.assign({ format: 'molstar-camera', structure: model.filename }, cameraNow());
    $('#view-note').textContent = sync.mode === 'live' ? 'Claude already sees this view through the workspace. Copy it only to use it elsewhere.' : 'Paste this into the chat and ask Claude to render the structure from this angle.';
    $('#view-text').value = '{"structure_studio_view": {\n' + Object.entries(v).map(([k, val]) => `  "${k}": ${JSON.stringify(val)}`).join(',\n') + '\n}}';
    $('#view-sheet').hidden = false; $('#view-copy').focus();
  });
  $('#view-copy').addEventListener('click', () => { const ta = $('#view-text'); navigator.clipboard.writeText(ta.value).then(() => toast('View copied'), () => { ta.focus(); ta.select(); toast('Press Ctrl/⌘+C to copy'); }); });
  $('#view-close').addEventListener('click', () => { $('#view-sheet').hidden = true; });
  $('#lightbox-close').addEventListener('click', () => { $('#lightbox').hidden = true; });
  for (const o of ['#view-sheet', '#lightbox']) $(o).addEventListener('click', (e) => { if (e.target === e.currentTarget) e.currentTarget.hidden = true; });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { $('#view-sheet').hidden = true; $('#lightbox').hidden = true; } });
  $('#btn-tools').addEventListener('click', (e) => {
    if (!viewer) return; const on = e.currentTarget.getAttribute('aria-pressed') !== 'true';
    e.currentTarget.setAttribute('aria-pressed', String(on));
    viewer.plugin.layout.setProps({ showControls: on, regionState: { left: 'collapsed', top: 'full', right: on ? 'full' : 'hidden', bottom: 'hidden' } });
  });
  $('#btn-open').addEventListener('click', () => $('#file-input').click());
  $('#file-input').addEventListener('change', async (e) => { const f = e.target.files && e.target.files[0]; if (f) await openFile(f); e.target.value = ''; });
  const stage = $('#stage');
  stage.addEventListener('dragover', (e) => { if (sync.mode === 'static') { e.preventDefault(); stage.classList.add('dragging'); } });
  stage.addEventListener('dragleave', (e) => { if (!stage.contains(e.relatedTarget)) stage.classList.remove('dragging'); });
  stage.addEventListener('drop', async (e) => { if (sync.mode !== 'static') return; e.preventDefault(); stage.classList.remove('dragging'); const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) await openFile(f); });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyBackground);
  new MutationObserver(applyBackground).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}
function setTab(name) {
  for (const t of ['3d', 'renders', 'files']) { $('#tab-' + t).setAttribute('aria-selected', String(t === name)); $('#panel-' + t).hidden = t !== name; }
  if (name === '3d' && viewer) requestAnimationFrame(() => viewer.handleResize());
}

/* ---------- Structure loading ---------- */
async function loadModel(bytes, filename, predicted) {
  let name = filename;
  if (/\.gz$/i.test(name)) { bytes = await gunzip(bytes); name = name.replace(/\.gz$/i, ''); }
  model = engine.loadModel(bytes, name, predicted);
  populateHeader(); populateLigands(); syncControls();
}
async function openFile(file) {
  try {
    status('Opening ' + file.name + '…');
    await loadModel(new Uint8Array(await file.arrayBuffer()), file.name, null);
    scene = engine.setScene({ color: scene.color });
    await draw({ fresh: true }); setTab('3d'); toast('Opened ' + file.name);
  } catch (e) { console.error(e); status(e.message || String(e), true); }
}

/* ---------- Renders and files ---------- */
async function fetchPacked(entry) {
  if (!Array.isArray(entry.asset_ids) || !entry.asset_ids.length || entry.asset_ids.length > LIMITS.parts) throw new Error('Invalid part count');
  const parts = []; let total = 0;
  for (const id of entry.asset_ids) {
    const bytes = await readResponse(await fetch('/_blob/' + encodeURIComponent(id)), Math.min(LIMITS.part, LIMITS.encoded - total));
    total += bytes.length; parts.push(bytes);
  }
  return decodeAsset(parts, entry);
}
async function savePacked(entry, button) {
  const label = button.textContent; button.disabled = true; button.textContent = 'Preparing…';
  try { const bytes = await fetchPacked(entry); await saveFile(entry.filename.replace(/\.[^.]+$/, '') + '.zip', new Blob([zipStore([[entry.filename, bytes]])])); }
  catch (e) { console.error(e); toast('Could not prepare ' + entry.filename + ': ' + (e.message || e)); }
  finally { button.disabled = false; button.textContent = label; }
}
/* Session bundles (structure-studio-session/1): JSON files whose entries carry gzip+base64 data. */
const bundleCache = new Map();
function fetchBundle(session) {
  if (!Array.isArray(session.asset_ids) || !session.asset_ids.length || session.asset_ids.length > LIMITS.parts) throw new Error('Invalid bundle part count');
  const key = session.asset_ids.join(',');
  if (!bundleCache.has(key)) {
    bundleCache.clear(); // Retain only the active bundle, not every downloaded scene.
    bundleCache.set(key, (async () => {
      const docs = []; let total = 0;
      for (const id of session.asset_ids) {
        const bytes = await readResponse(await fetch('/_blob/' + encodeURIComponent(id)), Math.min(LIMITS.part, LIMITS.encoded - total));
        total += bytes.length; docs.push(JSON.parse(new TextDecoder().decode(bytes)));
      }
      return parseBundles(docs);
    })().catch(error => { bundleCache.delete(key); throw error; }));
  }
  return bundleCache.get(key);
}
async function bundleFile(session, file) {
  return decodeBundle(await fetchBundle(session), file);
}
async function saveBundleFile(session, file, button) {
  const label = button.textContent; button.disabled = true; button.textContent = 'Preparing…';
  try {
    const bytes = await bundleFile(session, file);
    if (['mp4', 'gif', 'frames', 'animation'].includes(file.role)) await saveFile(file.filename, new Blob([bytes]));
    else await saveFile(file.filename.replace(/\.[^.]+$/, '') + '.zip', new Blob([zipStore([[file.filename, bytes]])]));
  }
  catch (e) { console.error(e); toast('Could not prepare ' + file.filename + ': ' + (e.message || e)); }
  finally { button.disabled = false; button.textContent = label; }
}
const ROLE_LABEL = { pse: 'PyMOL .pse', blend: 'Blender .blend', mp4: 'Save MP4', gif: 'Save GIF', frames: 'PNG frames', animation: 'Animation recipe' };
const movieURLs = new Set();
function actionButton(text, handler) { const b = document.createElement('button'); b.className = 'btn'; b.textContent = text; b.addEventListener('click', () => handler(b)); return b; }
function renderCard(r) {
  const fig = document.createElement('figure'); fig.className = 'render';
  const done = r.status === 'done' || (!r.status && (r.image || r.src));
  if (!done) fig.classList.add(r.status === 'failed' ? 'failed' : 'pending');
  const thumb = document.createElement('button'); thumb.className = 'thumb';
  if (done) {
    const img = document.createElement('img'); img.loading = 'lazy';
    img.src = r.src || '/_blob/' + r.image.asset_id; img.alt = `${r.label || r.preset} render`;
    thumb.append(img); thumb.setAttribute('aria-label', 'Enlarge ' + (r.label || r.preset));
    thumb.addEventListener('click', () => { $('#lightbox-img').src = img.src; $('#lightbox-img').alt = img.alt; $('#lightbox').hidden = false; $('#lightbox-close').focus(); });
  } else {
    thumb.disabled = true;
    const t = document.createElement('span'); if (r.status !== 'failed') t.className = 'pulse';
    t.textContent = r.status === 'failed' ? 'Render failed' + (r.error ? ': ' + r.error : '') : `Rendering ${r.preset || ''}… started ${r.started_at ? ago(r.started_at) : 'just now'}`;
    thumb.append(t);
  }
  const cap = document.createElement('figcaption');
  const name = document.createElement('span'); name.className = 'name'; name.textContent = r.label || r.preset; cap.append(name);
  if (r.note || r.description) { const d = document.createElement('span'); d.className = 'desc'; d.textContent = r.note || r.description; cap.append(d); }
  const spec = [r.preset && r.label !== r.preset ? r.preset : '', r.image && r.image.width ? `${r.image.width}×${r.image.height}` : r.spec || '', r.seconds ? `${Math.round(r.seconds)} s` : '', r.created_at ? ago(r.created_at) : ''].filter(Boolean).join(' · ');
  if (spec) { const s = document.createElement('span'); s.className = 'spec'; s.textContent = spec; cap.append(s); }
  if (done) {
    const actions = document.createElement('div'); actions.className = 'card-actions';
    actions.append(actionButton('Save image', async () => {
      const blob = r.src ? new Blob([b64ToBytes(r.src.split(',')[1])]) : await fetch('/_blob/' + r.image.asset_id).then((x) => x.blob());
      await saveFile(r.filename || (r.image && r.image.filename) || 'render.png', blob);
    }));
    if (r.session) for (const f of r.session.files || []) actions.append(actionButton(ROLE_LABEL[f.role] || f.filename, (b) => saveBundleFile(r.session, f, b)));
    const movie = r.session && (r.session.files || []).find((f) => f.role === 'mp4');
    if (movie) actions.prepend(actionButton('Play animation', async (button) => {
      button.disabled = true;
      try {
        const bytes = await bundleFile(r.session, movie);
        if (!fig.isConnected) return;
        const url = URL.createObjectURL(new Blob([bytes], { type: 'video/mp4' })); movieURLs.add(url);
        const video = document.createElement('video'); video.src = url; video.controls = true;
        video.playsInline = true; video.style.width = '100%';
        video.loop = !!(r.animation && ['turntable', 'rock'].includes(r.animation.recipe));
        thumb.hidden = true; fig.insertBefore(video, cap);
        button.hidden = true; video.play().catch(() => {});
      } catch (e) { toast('Could not play animation: ' + (e.message || e)); }
      finally { button.disabled = false; }
    }));
    if (r.pse) actions.append(actionButton('PyMOL .pse', (b) => savePacked(r.pse, b)));
    if (r.blend) actions.append(actionButton('Blender .blend', (b) => savePacked(r.blend, b)));
    if (r.camera && viewer) actions.append(actionButton('View this angle', () => { setTab('3d'); setCamera(r.camera, 600); }));
    cap.append(actions);
  }
  fig.append(thumb, cap);
  return fig;
}
function buildGallery() {
  for (const url of movieURLs) URL.revokeObjectURL(url); movieURLs.clear();
  const list = sync.renders || DATA.renders || [];
  const done = list.filter((r) => r.status !== 'failed');
  $('#render-count').textContent = done.length ? String(done.length) : '';
  const g = $('#gallery'); g.replaceChildren();
  if (!list.length) {
    const p = document.createElement('p'); p.className = 'empty';
    p.textContent = 'No renders yet. Ask Claude in the chat, for example “render this view with mint_sky_assembly”, and it will appear here when it finishes.';
    g.append(p); return;
  }
  for (const r of list) g.append(renderCard(r));
}
function buildFiles() {
  const box = $('#files'); box.replaceChildren();
  if (sync.renders) {
    const ul = document.createElement('ul'); ul.className = 'file-list';
    const addRow = (label, size, entry, handler) => {
      const li = document.createElement('li'); const a = document.createElement('span'); a.className = 'fname'; a.textContent = label;
      const right = document.createElement('span'); right.className = 'fsize'; right.textContent = fmtBytes(size || 0);
      li.append(a, right);
      if (entry || handler) li.append(actionButton('Save', handler || ((b) => savePacked(entry, b))));
      ul.append(li);
    };
    const st = sync.meta && sync.meta.structure;
    if (st && st.asset_id) addRow(st.filename, st.bytes, { filename: st.filename, asset_ids: [st.asset_id], encoding: 'text', sha256: st.sha256 });
    const src = sync.meta && sync.meta.source_session;
    if (src && src.asset_ids) for (const f of src.files || []) addRow(`${f.filename} · uploaded session`, f.bytes, null, (b) => saveBundleFile(src, f, b));
    for (const r of sync.renders) {
      if (r.session) for (const f of r.session.files || []) addRow(`${f.filename} · ${r.label || r.preset}`, f.bytes, null, (b) => saveBundleFile(r.session, f, b));
      for (const role of ['pse', 'blend']) if (r[role]) addRow(`${r[role].filename} · ${r.label || r.preset}`, r[role].bytes, r[role]);
    }
    const p = document.createElement('p'); p.className = 'note';
    p.textContent = 'Open .pse files in PyMOL and .blend files in Blender 5.0 or newer; these download as .zip files. Movies, frames and animation recipes keep their original file formats.';
    if (!ul.children.length) { p.textContent = 'No session files yet. Renders made from the chat add their PyMOL session and Blender scene here.'; box.append(p); return; }
    box.append(ul, p); return;
  }
  const d = DATA.downloads; const p = document.createElement('p'); p.className = 'note';
  if (!d) { p.textContent = 'No session files are attached to this page.'; box.append(p); return; }
  const ul = document.createElement('ul'); ul.className = 'file-list';
  for (const f of d.contents) { const li = document.createElement('li'); const a = document.createElement('span'); a.className = 'fname'; a.textContent = f.name; const b = document.createElement('span'); b.className = 'fsize'; b.textContent = fmtBytes(f.size); li.append(a, b); ul.append(li); }
  const btn = document.createElement('button'); btn.className = 'btn primary'; btn.textContent = `Save ${d.filename} (${fmtBytes(d.size)})`;
  btn.addEventListener('click', () => saveFile(d.filename, new Blob([b64ToBytes(d.data)])));
  p.textContent = 'Session files bundled as one .zip.';
  box.append(ul, btn, p);
}

async function main() {
  window.studio = { get viewer() { return viewer; }, get model() { return model; }, get scene() { return scene; }, sync, resolve, buildScene };
  wire(); buildGallery(); buildFiles();
  if (DATA.initial_tab) setTab(DATA.initial_tab);
  try {
    const s = DATA.structure;
    let bytes = b64ToBytes(s.data); if (s.gzip) bytes = await gunzip(bytes);
    await loadModel(bytes, s.filename, DATA.predicted);
  } catch (e) { console.error(e); status('Could not read the embedded structure: ' + (e.message || e), true); return; }
  const connected = connect();
  try { await createViewer(); applyBackground(); }
  catch (e) { console.error(e); status(e.message || String(e), true); return; }
  await connected;
  /* Give the first database snapshots a moment so a saved shared camera beats the embedded one. */
  if (sync.db && sync.cameraKnown === undefined) await new Promise((r) => setTimeout(r, 600));
  const opening = sync.pendingCamera || DATA.camera || null;
  sync.pendingCamera = null;
  await draw({ fresh: true, camera: opening });
  sync.drawn = true;
  if (sync.pendingCamera) { await settleCamera(sync.pendingCamera); sync.pendingCamera = null; }
  /* A new workspace has no shared camera yet: record the opening view once so renders match the panel. */
  setTimeout(() => {
    if (sync.mode === 'live' && sync.cameraKnown === false && viewer) writeDoc('scene/camera', Object.assign(cameraNow(), { by: 'page', saved_at: new Date().toISOString(), initial: true }));
  }, 2500);
}
main();
})();
