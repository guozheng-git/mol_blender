# Proposed bounded personal Modal checkpoint — authorization pending

No deployment or paid execution is authorized by this file. Shared deployments,
hosted policy, OAuth configuration and publisher review are outside this checkpoint.

Use a separately selected personal Modal profile/workspace and a new personal app
named `blender-render-c3-personal`. The account/profile must be identified in the
authorization; do not infer the shared service's account. Deploy only the reviewed
C3 commit and its verified wheel/engine manifest from the contract runner's
`evidence.json`. Copy those exact hashes into the authorization record before
deployment. Existing deployments require drain and confirmed controller stop first.

The public fixture is `examples/1sps_pose.pse`, asserted chains A and D with expected
PTR/peptide content. Use the frozen host/Slurm acceptance bundle/spec, one logical
operation, at most one L4 leaf render, `GPU/AUTO/require_gpu=true`, and PNG only.
Maximum render duration: 600 seconds. Proposed total lifetime budget: USD 2,
including build, control, storage and GPU; stop if preflight cannot bound costs
within that ceiling. Proposed expiry: one hour after explicit authorization, with
no restart refill. No replacement render after an ambiguous response.

Download and verify PNG/manifest, record exact identities, actual GPU and timings,
then visually inspect. Stop the validation app, delete validation-owned ephemeral
resources and Volume contents after verified local retention, and revoke its
temporary credentials. Verify cleanup rather than relying on local declarations.

Real controller/Volume replacement, stopped-worker recovery, concurrent clients,
network interruption and provider billing remain separate unexecuted scenarios
unless explicitly added with a bounded budget/job count before execution.

Until this checkpoint passes: **Modal implemented and contract-tested; real
backend execution unverified.**
