import os
import subprocess
import sys


def test_application_imports_at_runtime(tmp_path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "BOT_TOKEN": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'runtime.sqlite3'}",
            "WEBHOOK_BASE_URL": "",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "import app.main; print('runtime-import-ok')"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "runtime-import-ok" in result.stdout
