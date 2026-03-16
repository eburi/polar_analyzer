# Python Development Instructions

## Virtual Environment

Always use a virtual environment for Python operations:
- Create with: `python -m venv venv`
- Activate with: `source venv/bin/activate` (Linux/macOS) or `venv\Scripts\activate` (Windows)
- Or use `uv venv` and `source .venv/bin/activate` for faster setup

When running Python scripts, tests, or installing packages, always ensure the venv is activated first.

## HomeAssistant Development

For HomeAssistant development:
- HomeAssistant instance is reachable via SSH: `root@primrose.local`
- The `ha` command is directly available on the instance (not through docker)
- Use SSH to connect and run `ha` commands directly on the host
