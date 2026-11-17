const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { gzipSync } = require('node:zlib');
const { webcrypto } = require('node:crypto');
const vm = require('node:vm');
globalThis.crypto ||= webcrypto;

const root = path.join(__dirname, '..');
const assets = path.join(root, 'plugins/structure-studio/skills/structure-studio/assets');
const createScene = require(path.join(assets, 'scene.js'));
const { decodeAsset, sha256, zipStore } = require(path.join(assets, 'artifact_files.js'));
const { gunzip, parseBundles, decodeBundle, readResponse } = require(path.join(assets, 'artifact_files.js'));
// Shared with co-pymol; viewer.py embeds it into each page as DATA.molecular_data.
const data = JSON.parse(fs.readFileSync(path.join(__dirname, '../co_pymol/scene/molecular-data.json')));
const blankPdb = new TextEncoder().encode(
  'ATOM      1  CA  GLY     1       0.000   0.000   0.000  1.00 90.00           C\n');

function nodes(node) { return [node, ...(node.children || []).flatMap(nodes)]; }

test('the browser scripts load without CommonJS and expose the app dependencies', () => {
  const context = vm.createContext({ TextDecoder, TextEncoder, Uint8Array, atob });
  for (const name of ['scene.js', 'artifact_files.js']) {
    vm.runInContext(fs.readFileSync(path.join(assets, name), 'utf8'), context);
  }
  assert.equal(typeof context.createStructureScene, 'function');
  assert.equal(typeof context.StructureStudioFiles.decodeAsset, 'function');
  assert.equal(context.createStructureScene(data).loadModel(blankPdb, 'blank.pdb').chains[0].id, '');
  new vm.Script(fs.readFileSync(path.join(assets, 'workspace.js'), 'utf8'));
});

test('plain-text structure downloads preserve exact UTF-8 bytes', async () => {
  const bytes = new TextEncoder().encode('data_model\n_struct.title "Protein α"\n#\n');
  const result = await decodeAsset([bytes.slice(0, 15), bytes.slice(15)], {
    encoding: 'text', filename: 'model.cif', sha256: await sha256(bytes),
  });
  assert.deepEqual(result, bytes);
  const zip = zipStore([['model.cif', result]]);
  assert.equal(new DataView(zip.buffer).getUint32(0, true), 0x04034b50);
});

test('legacy session parts still decompress and verify', async () => {
  const bytes = new TextEncoder().encode('session bytes');
  const encoded = new TextEncoder().encode(gzipSync(bytes).toString('base64'));
  const entry = { encoding: 'gzip+base64', filename: 'model.pse', sha256: await sha256(bytes) };
  assert.deepEqual(await decodeAsset([encoded.slice(0, 5), encoded.slice(5)], entry), bytes);
  await assert.rejects(decodeAsset([encoded], { ...entry, sha256: 'wrong' }), /checksum/);
  await assert.rejects(decodeAsset([encoded], { ...entry, encoding: 'unknown' }), /Unsupported/);
});

test('gzip expansion stops at the declared size and streamed downloads are bounded', async () => {
  await assert.rejects(gunzip(gzipSync(Buffer.alloc(1000000)), 16), /size limit/);
  await assert.rejects(readResponse(new Response(Buffer.alloc(500)), 16), /size limit/);
  await assert.rejects(gunzip(gzipSync(Buffer.from('data')).subarray(0, 12)), /./);
});

test('bundle parsing validates parts, metadata, hashes and dangerous filenames', async () => {
  const raw = new TextEncoder().encode('trusted session bytes');
  const entry = {filename:'test.pse', role:'pse', bytes:raw.length, sha256:await sha256(raw), encoding:'gzip+base64', parts:1, part:1, data:gzipSync(raw).toString('base64')};
  const doc = {format:'structure-studio-session/1', index:1, count:1, entries:[entry]};
  assert.deepEqual(await decodeBundle(parseBundles([doc]), entry), raw);
  for (const update of [{bytes:-1}, {bytes:2**40}, {parts:2**40}, {part:0}, {filename:'../x'}, {sha256:'bad'}]) {
    assert.throws(() => parseBundles([{...doc, entries:[{...entry, ...update}]}]));
  }
  assert.throws(() => parseBundles([{...doc, entries:[entry, entry]}]), /Duplicate/);
  assert.throws(() => parseBundles([{...doc, count:2}]), /missing/);
  assert.throws(() => parseBundles([{...doc, entries:[{...entry, parts:2}]}]), /Missing/);
  const small = {...entry, bytes:1};
  await assert.rejects(decodeBundle(parseBundles([{...doc, entries:[small]}]), small), /size limit/);
  await assert.rejects(decodeBundle(parseBundles([doc]), {...entry, sha256:'0'.repeat(64)}), /mismatch/);
});

for (const scene of [
  { color: 'plddt' },
  { color: 'chain', layers: [{ select: { kind: 'polymer' }, color: 'plddt', representation: 'cartoon' }] },
]) {
  test(`confidence resources accompany all references: ${JSON.stringify(scene)}`, () => {
    const engine = createScene(data);
    engine.loadModel(blankPdb, 'model.pdb');
    engine.setScene(scene);
    const { files } = engine.sceneFiles();
    const resources = new Map(files);
    const tree = JSON.parse(new TextDecoder().decode(resources.get('index.mvsj')));
    const references = nodes(tree.root).filter((node) => node.kind === 'color_from_uri');
    assert.ok(references.length);
    for (const node of references) assert.ok(resources.has(node.params.uri), node.params.uri);
    const colors = JSON.parse(new TextDecoder().decode(resources.get('plddt.json')));
    assert.equal(colors.length, 1);
  });
}

test('blank chain identity survives parsing, drawing and visibility edits', () => {
  const engine = createScene(data);
  assert.equal(engine.loadModel(blankPdb, 'blank.pdb').chains[0].id, '');
  const components = nodes(engine.buildScene().tree.root).filter((node) => node.kind === 'component');
  assert.equal(components[0].params.selector[0].auth_asym_id, '');
  engine.setScene({ hidden: [{ chain: [''] }] });
  assert.equal(nodes(engine.buildScene().tree.root).filter((node) => node.kind === 'representation').length, 0);
});

test('showing one chain preserves grouped and residue-specific exclusions', () => {
  const engine = createScene(data);
  for (const chain of ['A', '']) {
    const hidden = [{ chain: [chain, 'B'] }, { chain, residues: '1-5' }];
    assert.deepEqual(engine.showChain(hidden, chain), [{ chain: ['B'] }, { chain, residues: '1-5' }]);
    assert.deepEqual(hidden[0], { chain: [chain, 'B'] });
  }
});

test('BinaryCIF is rejected before replacing the current model', () => {
  const engine = createScene(data);
  engine.loadModel(blankPdb, 'blank.pdb');
  assert.throws(() => engine.loadModel(new Uint8Array([1]), 'model.bcif'), /convert BinaryCIF/);
  assert.ok(nodes(engine.buildScene().tree.root).some((node) => node.kind === 'representation'));
});

test('public CIF fixture produces visible polymer and ligand representations', () => {
  const engine = createScene(data);
  const model = engine.loadModel(fs.readFileSync(path.join(root, 'examples/1stp.cif')), '1stp.cif');
  assert.equal(model.ligands[0].key, 'BTN A300');
  assert.ok(nodes(engine.buildScene().tree.root).filter((node) => node.kind === 'representation').length >= 2);
});
