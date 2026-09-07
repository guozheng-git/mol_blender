# Troubleshooting

| Symptom | Check |
|---|---|
| `pymol2` is missing | Configure `setup --pymol-python` with PyMOL's interpreter. The service and PyMOL environments may differ. |
| Molecular Nodes or CCD is missing | Run setup with the pinned runtime, then `doctor`. A CCD hash mismatch is an error, not a reason to download a different snapshot silently. |
| Local Mac asks for Apptainer | Use the current wheel and `mode=local`. The legacy `use_apptainer=1` compatibility flag is ignored on Mac local mode. |
| Render loses secondary structure | Inspect manifest `dss_ran`, `secondary_structure`, and `polymer_backend`. Preset 1 must use `mn_cartoon`; it must not silently become a tube. |
| Ligand has ball-joint artifacts | Check CIF was selected and `sticks_backend` is `mn_native_sticks`. Atom spheres are disabled for ligand sticks. |
| Colors differ | Enable `color_follow=1` for PyMOL colors. Dried paint has its own green secondary-structure palette when color-follow is off. |
| Camera differs | Use a PSE or PyMOL export to carry the saved view. Check the manifest's input view, viewport, orthoscopic flag, camera matrix, and output dimensions. Raw CIF/PDB inputs do not contain a PyMOL camera. |
| GPU render is unexpectedly slow | Check `cycles_device` in the manifest. `AUTO` chooses an available device; an unavailable GPU can fall back to CPU locally. Public jobs require CUDA. |
| SSH to a GPU node is denied | Use the profile's `transport=slurm`; it proxies through an overlapping step in your own allocation. |
| Allocation is pending or died | Reconnect to the same profile. Do not submit a second render just because the first job is pending. `attach_only=1` reports an absent service without allocating. |
| Cannot attach after changing runtime | Stop the existing service before switching versions or SIF images. |
| HTTP 401 | Reauthenticate, or check the private profile's token file. Do not place bearer tokens in URLs. |
| HTTP 402/429 | The lifetime credit allowance, ten-render daily quota, or global queue capacity is exhausted. Upload count and daily GPU minutes have no quota. The verified testing owner has no daily render limit; the project budget still applies. |
| Download link expired or already used | Files are deleted after download or fifteen minutes after completion. Check your downloads; the service does not archive rendered files. |
| ChatGPT preview shows a DNS error after an endpoint upgrade | Refresh the plugin in ChatGPT settings, then reload the conversation. Operators should configure a short, dedicated widget origin; the Modal HTTP Server URL can make ChatGPT's derived hostname too long. |
| Repeated import permissions or repeated "I'll render" announcements | Use the current plugin and refresh the ChatGPT connection's tools. Call `render_structure` once, then only status/result for that job. Enabling Always allow does not repair a submission loop. The server also deduplicates identical requests with changed retry keys. |
| Green plugin asks to use Blender Render Private Beta | Private Beta is the registered service connection referenced by the green plugin. ChatGPT asks permission under that connection's name. The obsolete disconnected Blender Render connection is not needed. |
| A job is cancelled | Cancellation is separate from a structure or preset failure. Check whether a user/operator cancelled it. If accidental duplicates existed, retrieve the original completed job rather than reporting the cancelled copy as the entire request's outcome. |
| Temporary input not found | Submit an import within five minutes. Inputs are deleted after rendering or lost when a transient worker restarts. Reimport for a new render, but reuse the original idempotency key when checking an uncertain submission. |
| PSE conversion isolation is unavailable | Configure the lab host's supported Apptainer namespace isolation. Do not retry an untrusted PSE in the gateway's host Python. |
| Interrupted service after a paid dispatch | That execution is marked failed and its reservation remains charged. It is not automatically replayed. |
| Rootless container build reports a GLIBC mismatch | Use the pinned compatible base and a supported Apptainer installation. See the upstream fakeroot guide; do not request host root privileges just to run a render. |

Attach a manifest and software versions to a bug report. Review it first for private filenames, object names, paths, or scientific metadata. Keep input structures private unless you can share them publicly. The supplied examples are suitable for public reports.
