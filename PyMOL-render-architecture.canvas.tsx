import { Button, Card, CardBody, CardHeader, Code, Divider, Grid, H1, H2, Row, Stack, Table, Text, useCanvasState, useHostTheme } from "cursor/canvas";

const ownership = [
  ["co_pymol/", "Live PyMOL, MCP commands, selection and session capture", "Thin client of the shared catalog, recipe compiler and job API"],
  ["Structure Studio", "Workspace UI, file import, headless session setup, artifact presentation", "Same shared compiler and job API; its UI owns cards and downloads"],
  ["render_shared/ — proposed", "Versioned data contracts, defaults, preset metadata, recipe compiler", "Python 3.10+, no bpy, MCP, web framework or scheduler imports"],
  ["blender_render/", "Public rendering facade, request resolution, local execution, optional cluster dispatch", "One scene renderer; cluster authentication and submission stay in cluster/"],
];
const current = [
  ["Actual Blender rendering", "Already shared", "local.render_payload → developer.preview → blender_custom_render"],
  ["Default values and limits", "Centralized in this change", "render_defaults.yaml → checked generated runtime data"],
  ["Animation compilation", "Still duplicated", "co_pymol/core/animation.py and Studio scripts/animation_recipes.py"],
  ["Scene preparation", "Still split", "co_pymol/utils/pymol/animate.py and Studio scripts/animation_session.py"],
  ["Job and result lifecycle", "Still split", "co_pymol/core/blender_worker.py and Studio scripts/studio.py"],
  ["Preset catalog", "Canonical renderer catalog exists", "Studio ships a generated catalog; PyMOL queries the renderer"],
];
const api = [
  ["catalog()", "Versioned presets, scene recipes, animation recipes and supported options"],
  ["prepare(snapshot, settings)", "Validated, resolved RenderPlan with explicit camera, timeline, style and hashes"],
  ["submit(plan, backend)", "Durable local receipt; local execution or cluster submission through one dispatcher"],
  ["status(receipt)", "Saved/observed progress with local acceptance distinguished from GPU execution"],
  ["result(receipt)", "Recover the existing job and return verified artifacts"],
  ["cancel(receipt)", "Request cancellation and retain its confirmed or pending state"],
];

export default function RenderArchitecture() {
  const theme = useHostTheme();
  const [tab, setTab] = useCanvasState<"ownership" | "entrypoints" | "migration">("render-architecture-tab", "ownership");
  return <Stack gap={20} style={{ padding: 24, maxWidth: 1120, margin: "0 auto", color: theme.text.primary }}>
    <Stack gap={6}>
      <Text size="small" tone="secondary">Design proposal · pymol-merge · 13 November 2026</Text>
      <H1>One rendering contract, two clients</H1>
      <Text>Keep <Code>co_pymol</Code> and <Code>blender_render</Code> as sibling packages. Share the recipe implementation and rendering contract through a small dependency-light library. Keep one release artifact while the interfaces settle.</Text>
      <Text tone="secondary">The notification removal and shared defaults are implemented. The architecture below is a proposed next stage.</Text>
    </Stack>

    <Grid columns="repeat(auto-fit, minmax(240px, 1fr))" gap={16}>
      <Card><CardHeader>PyMOL client</CardHeader><CardBody><Text>Live session → capture camera, enabled objects, colours and molecular states.</Text></CardBody></Card>
      <Card><CardHeader>Structure Studio client</CardHeader><CardBody><Text>Workspace or saved session → the same scene snapshot through headless PyMOL.</Text></CardBody></Card>
    </Grid>
    <Stack gap={8} style={{ padding: 18, background: theme.fill.tertiary }}>
      <Text weight="semibold" style={{ color: theme.accent.primary }}>Both clients → shared preparation and recipe compiler → RenderPlan</Text>
      <Text>One compiler samples camera motion, state indices, holds, transitions and cutaway opacity. A shared PyMOL capture helper accepts the chosen session; the client chooses live or headless access.</Text>
      <Text weight="semibold">RenderPlan → renderer job API → local or cluster → the same Blender scene renderer</Text>
      <Text tone="secondary">A Python facade and a versioned JSON stdio interface expose the same operations. PyMOL can launch the renderer interpreter without importing its execution dependencies.</Text>
    </Stack>

    <Row gap={8} wrap>
      <Button variant={tab === "ownership" ? "primary" : "secondary"} onClick={() => setTab("ownership")}>Responsibilities</Button>
      <Button variant={tab === "entrypoints" ? "primary" : "secondary"} onClick={() => setTab("entrypoints")}>Public contract</Button>
      <Button variant={tab === "migration" ? "primary" : "secondary"} onClick={() => setTab("migration")}>Migration</Button>
    </Row>

    {tab === "ownership" && <Stack gap={14}>
      <H2>Package responsibilities</H2>
      <Table headers={["Owner", "Responsibility", "Boundary"]} rows={ownership} />
      <H2>What is shared today</H2>
      <Table headers={["Area", "Current state", "Implementation"]} rows={current} />
      <Text tone="secondary">The pure recipe compiler belongs in render_shared; host capture can be a separate, explicitly invoked helper accepting a PyMOL cmd object. Importing the shared contract must not initialize a host or connect to a service.</Text>
    </Stack>}

    {tab === "entrypoints" && <Stack gap={14}>
      <H2>Separate look, scene preparation and motion</H2>
      <Table headers={["Concept", "Meaning", "Examples"]} rows={[
        ["Preset", "Visual style, with a stable identifier and revision", "Mint sky assembly, cyan mint cutaway"],
        ["Scene recipe", "Reusable preparation and framing", "Overview, binding-site close-up, cutaway"],
        ["Animation recipe", "A timeline applied to the prepared scene", "Rock, turntable, reveal, states, scenes"],
        ["Quality profile", "Output defaults, explicitly recorded in the plan", "Preview or full; explicit width, height and samples win"],
      ]} />
      <H2>Proposed public operations</H2>
      <Table headers={["Operation", "Result"]} rows={api} />
      <Text>Requests compose a scene snapshot, preset, optional scene and animation recipes, quality and explicit overrides. A still has no animation timeline. Both clients receive the same resolved settings and artifact schema.</Text>
      <Text tone="secondary">Illustrative API names. Keep local trusted PSE loading in the capture stage; cluster requests contain data only. There is one receipt and one job lifecycle regardless of the submitting UI.</Text>
    </Stack>}

    {tab === "migration" && <Stack gap={16}>
      <H2>Extract the common behavior in reviewable steps</H2>
      <Text><Text weight="semibold">1. Shared recipes and capture.</Text> Move the pure compiler first, then reuse the same capture helper. Preserve established frame counts and camera math. Make cutaway display differences explicit parameters; PyMOL currently uses shell transparency while Studio uses lid opacity.</Text>
      <Text><Text weight="semibold">2. Shared render facade.</Text> Extract job submission, recovery, cancellation and result verification from the PyMOL worker and Studio orchestration. Keep the existing local.py renderer and cluster client behind it.</Text>
      <Text><Text weight="semibold">3. Thin clients and packaging.</Text> Route both clients through the facade. Generate any offline catalogs from its canonical data. Remove copied algorithms and ad hoc source-file loading.</Text>
      <Divider />
      <Text weight="semibold">Acceptance condition</Text>
      <Text>The same captured scene, recipe, preset and overrides produce the same normalized RenderPlan through either client: identical views, frame/state indices, cutaway opacity and resolved style. Separate runtime tests establish that both adapters execute and present that plan correctly.</Text>
      <Text weight="semibold">Packaging condition</Text>
      <Text>Include the shared library and its data in engine identity checks and private source snapshots. Package one matched release, with optional dependencies for PyMOL and Studio. Updating shared code must invalidate an incompatible warm worker or lab engine.</Text>
    </Stack>}

    <Divider />
    <Text size="small" tone="tertiary">Source: local branch implementation, co_pymol/core/animation.py, co_pymol/core/blender_worker.py, blender_render/local.py, and Structure Studio scripts. This proposal adds no network calls, background jobs or architectural code changes.</Text>
  </Stack>;
}
