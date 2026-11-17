import os

# On some AMD GPUs (ROCm) PyTorch's efficient attention is gated as experimental; without it attention
# falls back to the slow path (training measured 22 s/step against 8 s/step, 2026-11-16).
os.environ.setdefault("TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL", "1")
