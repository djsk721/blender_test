# GPU Safety and Resource Management

## 1. Operational Context

This workstation uses dual `NVIDIA GeForce RTX 3090` GPUs shared with local LLM inference services (`strata`, `ollama`). The host has previously experienced severe NVIDIA driver/hardware fault events involving:

- UVM global fatal errors
- GSP RPC failures
- `NV_ERR_GPU_IS_LOST`
- A GPU disappearing from `nvidia-smi`
- Unexpected termination of the `strata` inference engine

This document does **not** claim that Blender caused those failures. Instead, `.opencode/mcp/blender/gpu_manager.py` implements conservative pre-launch health checks and safe fallback behavior so Blender never compounds GPU instability or interferes with co-resident inference workloads.

## 2. Conservative Health Checks

Before launching Blender on GPU, `get_gpu_snapshot()` performs read-only inspection (5-second timeout):

1. **Driver/NVML Query**: Runs `nvidia-smi --query-gpu=index,name,uuid,driver_version,pci.bus_id,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu,pstate --format=csv,noheader,nounits`.
2. **Error Pattern Detection**: Scans return codes, timeouts, and output text for fault markers (`NV_ERR_GPU_IS_LOST`, `GPU is lost`, `Unable to determine the device handle`, `Failed to initialize NVML`, `Driver/library version mismatch`, `falling off the bus`, `GSP RPC`, `Unknown Error`).
3. **PCI Device Cross-Check**: Compares `/proc/driver/nvidia/gpus` directory count (when present) against `nvidia-smi` reported GPU count. If `/proc/driver/nvidia/gpus` shows more GPUs than `nvidia-smi` reports, the GPU subsystem is marked `"degraded"`.
4. **Per-GPU Validation**: Rejects any GPU row with unreadable total/free VRAM or error `pstate`, marking `healthy: False` (never treated as available VRAM).
5. **Thermal Guard**: Excludes any GPU whose temperature meets or exceeds `gpu.max_safe_temp_c` (default `88` °C).
6. **Expected GPU Count (Optional)**: When `gpu.expected_gpu_count` (`BLENDER_EXPECTED_GPU_COUNT`) is set, marks health `"degraded"` if fewer healthy GPUs are observed.

## 3. Fallback & Failure Policies

- When `health.status` is `"unhealthy"`, `"degraded"`, or `"unavailable"`, `select_launch_gpu_config()` **refuses to start GPU-intensive Blender work**:
  - Under `render_device: "AUTO"` and `unhealthy_gpu_policy: "cpu_fallback"` (default): falls back to CPU + software viewport (`CUDA_VISIBLE_DEVICES=""`, `LIBGL_ALWAYS_SOFTWARE=1`, and `VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json` when available).
  - Under `render_device: "GPU"` or `unhealthy_gpu_policy: "fail"`: blocks Blender launch cleanly with an explicit diagnostic error.
- When healthy GPUs have less than `min_free_vram_error_mib` (default `512` MiB), `vram_low_policy` (`"cpu_fallback"`, `"warn"`, or `"fail"`) controls whether Blender falls back to CPU, warns, or refuses launch.
- Every launch decision is recorded in `state.json` under `gpu_decision` (including `health_status`, `health_reasons`, `fallback_triggered`, `fallback_reason`, and `isolation_notes`).

## 4. Non-Interference Guarantees

- `gpu_manager.py`, `process_manager.py`, and `lifecycle_manager.py` **never**:
  - Terminate, signal, or renice `strata`, `ollama`, or any external GPU process
  - Execute GPU resets (`nvidia-smi --gpu-reset`), kernel driver reloads (`rmmod`/`modprobe`), or system reboots
  - Run GPU stress tests

## 5. Limitations of GPU Device Isolation

`CUDA_VISIBLE_DEVICES=<selected_gpu_index>` scopes **CUDA and OptiX compute device visibility only**. It has important limitations on multi-GPU Linux hosts:

1. **OpenGL / GLX / EGL**: Xvfb + NVIDIA GLX/EGL drivers may still initialize a context on the primary display GPU regardless of `CUDA_VISIBLE_DEVICES`.
2. **Vulkan**: Vulkan ICD loaders enumerate GPUs independently of `CUDA_VISIBLE_DEVICES` unless `VK_ICD_FILENAMES` (e.g., Mesa `lvp_icd.x86_64.json` in software fallback) or Vulkan device selection extensions are used.
3. **Cycles vs. Viewport (EEVEE)**: `apply_cycles_config()` configures Cycles preferences (`OPTIX`/`CUDA` vs. `CPU`), whereas EEVEE and Blender's 3D Viewport rely on OpenGL/Vulkan unless `LIBGL_ALWAYS_SOFTWARE=1` is active.
4. **Shared PCIe / Driver Fault Domain**: Even when isolated to GPU 1 via `CUDA_VISIBLE_DEVICES=1`, both GPUs share the kernel NVIDIA driver (`nvidia.ko` / `nvidia_uvm.ko`), so a host-level UVM/GSP fault impacts all CUDA clients on the system.
