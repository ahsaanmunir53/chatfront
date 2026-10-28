"""Muse Glimmer 30B on Modal, behind vLLM's OpenAI-compatible server.

Chatfront's payment watcher and customer simulator call it (features 30 and 110); when it is
down they fail over to Muse Spark Open (feature 114). It runs on one H100 from the FP8
checkpoint and scales to zero after five idle minutes, so the hackathon's Modal credits pay
only for the time it works.

Once, in the Modal dashboard: Secrets > Create new secret > Custom, named chatfront-glimmer,
with one value VLLM_API_KEY (any long random string; you choose it). Then, from this folder:

    pip install modal && modal setup
    modal deploy modal_glimmer.py

Modal prints the server's URL. In Render's Environment (and api/.env to try it locally) set
MODAL_GLIMMER_BASE_URL to that URL followed by /v1, and MODAL_GLIMMER_API_KEY to the same
VLLM_API_KEY. The first call after an idle spell waits for the model to load.
"""

import os
import subprocess

import modal

MODEL = "RedHatAI/Muse-Glimmer-30B-FP8-block"  # FP8 weights of meta-models/Muse-Glimmer-30B
# Chatfront asks for the model by GLIMMER_MODEL, which defaults to the first name.
SERVED_AS = ["meta-models/Muse-Glimmer-30B", "muse-glimmer"]
VLLM = "vllm==0.28.0"  # the first release with Glimmer's parsers
MINUTES = 60
PORT = 8000

image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install(VLLM)
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)
weights = modal.Volume.from_name("huggingface-cache", create_if_missing=True)
compiled = modal.Volume.from_name("vllm-cache", create_if_missing=True)

app = modal.App("chatfront-glimmer")


@app.server(
    image=image,
    gpu="H100:1",
    scaledown_window=5 * MINUTES,
    startup_timeout=15 * MINUTES,
    volumes={"/root/.cache/huggingface": weights, "/root/.cache/vllm": compiled},
    secrets=[modal.Secret.from_name("chatfront-glimmer")],
    port=PORT,
    target_concurrency=32,
    unauthenticated=True,  # Modal lets requests in; vLLM checks VLLM_API_KEY on each one
)
class Glimmer:
    @modal.enter()
    def start(self) -> None:
        command = [
            "vllm",
            "serve",
            MODEL,
            "--served-model-name",
            *SERVED_AS,
            "--host",
            "0.0.0.0",  # noqa: S104 - inside the container, where Modal routes requests
            "--port",
            str(PORT),
            "--max-model-len",
            "32768",  # Chatfront's longest call is far shorter; the rest is room for batches
            "--gpu-memory-utilization",
            "0.92",
            "--enable-auto-tool-choice",
            "--tool-call-parser",
            "muse_glimmer",
            "--reasoning-parser",
            "muse_glimmer",
            "--generation-config",
            "auto",
            "--api-key",
            os.environ["VLLM_API_KEY"],
        ]
        self.process = subprocess.Popen(command)  # noqa: S603 - a fixed command, no user input

    @modal.exit()
    def stop(self) -> None:
        self.process.terminate()
