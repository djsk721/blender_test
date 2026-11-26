# Autonomous production recovery, 2026-11-26 KST

The local and remote `main` both pointed to `189f6ecb84bd9c93bfe82cfbf47f35381eeeef43`. Git preserves the Cycle 1–8 code. Git does not contain the run's ignored Blender files and renders.

| Evidence | What it establishes |
|---|---|
| Gemini CLI log `cli-20261125_180001.log:136` | The previous objective was the autonomous Qwen3.8-Flash-Next asset workflow. |
| OpenCode `opencode.log:133561`, 04:41:20 KST | Iteration 007 invoked the final review with its studio render paths. This is the latest run with a logged final review invocation. |
| OpenCode `opencode.log:134660`, 05:14:21 KST | Iteration 008 requested detailed-modeling review of the Stage 3 renders. The logged reviewer model was Qwen. |
| OpenCode `opencode.log:134733` and `:134952`, 05:15:03 and 05:19:27 KST | Iteration 008 started a geometry correction and later invoked Stage 4 materials. The Stage 4 invocation by itself cannot establish completion. |
| Local `output/autonomous_harness/` and historical paths in the logs | The logged `qwen_adaptive_base_mesh/iteration_007` and `iteration_008` roots were absent. Neither a completed iteration report nor a final score could be recovered there. |
| Repository MCP autosave directory | Four nontrivial Blender libraries exposed only a generic `Scene`, with no named production scene. Two 15-byte files were also present. None can be tied to the target or approved as a production checkpoint. |
| Ignored `images.jpg`, SHA-256 `00f0dadea0650f456e99db8a18eb8891119bca0fbad132d675fa4953dd32683e` | The existing reference depicts Zelda from an existing game. It remains in place. |

**Last successful cycle and checkpoint.** Cycle 7 reached the final-review request. Cycle 8 reached the lookdev request. The logs alone cannot establish either cycle's final verdict, the last completed independent review, or a valid restorable asset checkpoint.

**Overnight shutdown.** No exit record could be tied to the last Cycle 8 request. Intentional termination, OpenCode termination, provider timeout or quota, Strata GPU allocation failure, Blender/MCP disconnection, host OOM or crash, and a harness exception remain unconfirmed. The available journal was permission-limited. OpenCode logged an unresponsive background service at 09:21:49 KST (`opencode.log:135162`), roughly four hours after the last Cycle 8 invocation and under a different run ID. A later diagnostic run failed in NVIDIA Vulkan `vkCreateDevice`; that was a separate reproducible startup failure and does not explain the overnight stop.

**Live dependency checks.** The Strata model endpoint returned `SC117-Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_S`. The exact requested Qwen and GPT model IDs were registered in OpenCode and returned `READY` in separate probes (34.76 s and 7.57 s). The two RTX 3090s each had roughly 4 GiB free at the check. The local CC0 library's SHA-256 matched the manifest. An original text-only calibration prop was used to test the implementation independently of the unavailable historical assets.

## Supervised validation with an original test prop

The test used a separate runtime with CPU rendering and software Vulkan. The first CPU-only attempt still initialized the NVIDIA Vulkan viewport and crashed at startup. Software viewport mode allowed the isolated MCP session to start. The production preflight then passed, including Qwen's structured health response and native Blender MCP loading of the SHA-256-verified CC0 mesh `GEO-body_female_stylized`.

Qwen authored and Blender MCP executed the calibration prop. The scene audit showed 2 export meshes, 7,246 triangles, 3 materials, and UV layers on every export mesh. The artifact was saved and reopened as `calibration_verified.blend`. GPT reviewed four rendered views in an independent context. The modeling-stage score was 96/100. The final frozen rubric scored 80/100, with no reported critical defects and with the following category scores.

| Category | Score |
|---|---:|
| Design fidelity | 23/25 |
| Silhouette and proportions | 17/20 |
| Geometry and modeling detail | 14/20 |
| Materials and textures | 10/15 |
| Artistic coherence and polish | 12/15 |
| Game asset readability | 4/5 |

GPT assigned a structural repair at the brass loop seats. The first Qwen repair exceeded its 145-second budget. The narrower retry invoked the render operator before editing and failed because the restored scene had no active camera. Blender reported a successful rollback. The retained checkpoint's SHA-256 stayed `7b9d4b46f7da47e3112744c86ad9cb06a2335ad7e4dbf5e1e92938c44f321de6`. The refinement stopped after those two attempts. There is no verified after image, score improvement, or approved GLB from this fixture. The harness now rejects direct producer render calls before Blender execution.

Local evidence is under `output/supervision-verified-fixture-nov26/`. This includes the Qwen source and execution traces in `scripts/`, the GPT work orders in `handoffs/`, review JSON in `reviews/`, and the initial turntable contact sheet in `renders/before/`. The preflight report is under `output/supervision-production-preflight-nov26/`.

GPU policy tests use the default configuration. Tests starting real Blender use CPU rendering with a software viewport. The legacy host-wide lifecycle suite was excluded because it reloads the shared OpenCode service and can discard sessions in its default Blender runtime. The isolated integration tests exercised native Blender MCP, base meshes, renders, and GLB round-trips.
