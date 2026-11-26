"""Harness V3: Qwen-only asset production under independent Claude review.

Responsibilities are split into small modules so each can be tested on its own:

- ``status``          explicit stage status ledger (never PASS without execution)
- ``provenance``      model-role policy and script provenance (only the producer's code executes)
- ``script_validator`` static validation of producer Blender Python before execution
- ``checkpoints``     hashed checkpoints, best-technical vs best-reviewed, state consistency
- ``correction``      bounded, quality-driven correction control (stagnation/regression)
- ``reviewer``        independent visual review prompt and strict parsing
- ``work_orders``     compact, asset-agnostic Qwen work orders per modeling phase
- ``agents``          isolated single-flight agent invocation
- ``executor``        Blender MCP execution of producer scripts, renders and checkpoints
- ``pipeline``        the phase orchestrator
"""
