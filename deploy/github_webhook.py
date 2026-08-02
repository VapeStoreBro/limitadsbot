import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path

from aiohttp import web

REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "VapeStoreBro/limitadsbot")
WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
PATH_SECRET = os.environ["DEPLOY_PATH_SECRET"]
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", "/root/limitadsbot"))
SERVICE = os.environ.get("SYSTEMD_SERVICE", "limitadsbot.service")
HOST = os.environ.get("DEPLOY_HOST", "0.0.0.0")
PORT = int(os.environ.get("DEPLOY_PORT", "9102"))
LOCK = asyncio.Lock()


def valid_signature(body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


async def run(*args: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=PROJECT_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode, output.decode(errors="replace")[-8000:]


async def deploy(request: web.Request) -> web.Response:
    body = await request.read()
    if not valid_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise web.HTTPUnauthorized(text="invalid signature")
    payload = json.loads(body)
    if payload.get("repository", {}).get("full_name") != REPOSITORY:
        raise web.HTTPBadRequest(text="wrong repository")
    if payload.get("ref") != "refs/heads/main":
        return web.json_response({"ok": True, "ignored": True})

    async with LOCK:
        commands = [
            ("git", "fetch", "origin", "main"),
            ("git", "reset", "--hard", "origin/main"),
            (str(PROJECT_DIR / ".venv/bin/pip"), "install", "-r", "requirements.txt"),
            (str(PROJECT_DIR / ".venv/bin/python"), "-m", "compileall", "-q", "app", "deploy"),
            ("systemctl", "restart", SERVICE),
        ]
        logs = []
        for command in commands:
            code, output = await run(*command)
            logs.append({"command": " ".join(command), "code": code, "output": output})
            if code != 0:
                return web.json_response({"ok": False, "logs": logs}, status=500)
    return web.json_response({"ok": True, "logs": logs})


app = web.Application(client_max_size=2 * 1024 * 1024)
app.router.add_post(f"/deploy/{PATH_SECRET}", deploy)
app.router.add_get("/health", lambda _: web.json_response({"ok": True, "service": "limitads-deploy"}))

if __name__ == "__main__":
    web.run_app(app, host=HOST, port=PORT)
