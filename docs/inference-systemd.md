# Running the inference server as a systemd-user service

Worklog talks to a separate inference server over HTTP. The repo's
`scripts/start-inference.sh` launches `llama-server` with the BitNet
GGUF model. To keep it running in the background across reboots,
register it as a systemd-user unit.

Save this file as `~/.config/systemd/user/worklog-inference.service`:

```ini
[Unit]
Description=Worklog inference server (llama-server + BitNet)
After=default.target

[Service]
Type=simple
ExecStart=%h/projects/worklog-ai/scripts/start-inference.sh
Restart=on-failure
RestartSec=5
# Tunables — uncomment to override.
# Environment=BITNET_DIR=%h/projects/BitNet
# Environment=BITNET_MODEL=%h/projects/BitNet/models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf
# Environment=INFERENCE_HOST=127.0.0.1
# Environment=INFERENCE_PORT=8080
# Environment=INFERENCE_CTX=2048
# Environment=INFERENCE_THREADS=4

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now worklog-inference.service
journalctl --user -u worklog-inference -f   # follow logs
```

Check it from the worklog side:

```bash
wl doctor
# expect: Inference URL: http://127.0.0.1:8080/v1
#         Inference reachable: True
#         Inference model: models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf
```

Switching to a different backend (e.g. ollama, vLLM) only needs an
environment change on the worklog side:

```bash
export WORKLOG_INFERENCE_URL=http://127.0.0.1:11434/v1   # ollama
export WORKLOG_INFERENCE_MODEL=llama3.1
```

Worklog auto-detects the model id on startup if `WORKLOG_INFERENCE_MODEL`
is unset.
