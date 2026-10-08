"""Developer tasks that work the same on Windows, macOS and Linux. `make <task>` calls these.

    python scripts/dev.py up        start the stack and seed it (first run: 10 to 15 minutes)
    python scripts/dev.py up-langfuse   also start self-hosted Langfuse (about 2 GB more)
    python scripts/dev.py seed      seed again (skips what exists)
    python scripts/dev.py down      stop, keep data
    python scripts/dev.py reset     stop and delete all data, including the week's files
    python scripts/dev.py test      unit and contract tests (no stack needed)
    python scripts/dev.py itest     contract tests against the running Fineract and Odoo
    python scripts/dev.py lint      ruff
    python scripts/dev.py urls      where everything is
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "services" / "core"
URLS = """
  Fineract API   http://127.0.0.1:18080/fineract-provider/api/v1   (tenant: default, user from .env)
  Odoo ledger    http://127.0.0.1:18069                             (database: ledger, user from .env)
  Jaeger         http://127.0.0.1:16686
  Postgres       127.0.0.1:15432
  Week's files   data/inbox/   (truth for evals: data/truth/)
"""


def run(*cmd: str, cwd: Path = ROOT, env: dict | None = None) -> None:
    print("$", " ".join(cmd), flush=True)
    code = subprocess.call(cmd, cwd=cwd, env={**os.environ, **(env or {})})
    if code:
        sys.exit(code)


def python() -> str:
    for candidate in [CORE / ".venv" / "Scripts" / "python.exe", CORE / ".venv" / "bin" / "python"]:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def ensure_env() -> None:
    """Create .env from .env.example, or add keys the example has gained since. Random secrets."""
    import base64
    import re
    import secrets

    env_path = ROOT / ".env"
    have = env_path.read_text() if env_path.exists() else ""
    present = {line.split("=", 1)[0] for line in have.splitlines() if "=" in line and not line.startswith("#")}
    values: dict[str, str] = {}
    added = []
    for line in (ROOT / ".env.example").read_text().splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in present:
            continue
        value = re.sub(r"change-me-[a-z]+", lambda _: secrets.token_urlsafe(18), value)
        if key == "LANGFUSE_ENCRYPTION_KEY":
            value = secrets.token_hex(32)
        values[key] = value
        added.append(key)
    current = dict(line.split("=", 1) for line in have.splitlines() if "=" in line and not line.startswith("#"))
    if "LANGFUSE_AUTH" in values:
        pk = values.get("LANGFUSE_PUBLIC_KEY") or current.get("LANGFUSE_PUBLIC_KEY", "")
        sk = values.get("LANGFUSE_SECRET_KEY") or current.get("LANGFUSE_SECRET_KEY", "")
        values["LANGFUSE_AUTH"] = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    if not added:
        return
    if not have:
        text = (ROOT / ".env.example").read_text()
        for key, value in values.items():
            text = re.sub(rf"^{key}=.*$", lambda _m, v=value, k=key: f"{k}={v}", text, flags=re.M)
        env_path.write_text(text)
        print("created .env with random passwords")
    else:
        with env_path.open("a") as f:
            f.write("\n# added by scripts/dev.py\n" + "".join(f"{k}={values[k]}\n" for k in added))
        print("added to .env:", ", ".join(added))


def main(task: str) -> None:
    if task == "up":
        ensure_env()
        run("docker", "compose", "up", "-d", "--wait")
        run("docker", "compose", "run", "--rm", "--build", "seed")
        print(URLS)
    elif task == "up-langfuse":
        ensure_env()
        run("docker", "compose", "-f", "compose.yaml", "-f", "compose.langfuse.yaml", "up", "-d", "--wait")
        print(URLS + "  Langfuse       http://127.0.0.1:3000   (admin@repaydesk.local, password in .env)\n")
    elif task == "seed":
        run("docker", "compose", "run", "--rm", "--build", "seed")
    elif task == "down":
        run("docker", "compose", "down")
    elif task == "reset":
        run("docker", "compose", "down", "-v")
        shutil.rmtree(ROOT / "data", ignore_errors=True)
    elif task == "test":
        run(python(), "-m", "pytest", "-q", cwd=CORE)
    elif task == "itest":
        run(python(), "-m", "pytest", "-q", "-m", "integration", cwd=CORE, env={
            "FINERACT_URL": "http://127.0.0.1:18080/fineract-provider/api/v1",
            "ODOO_URL": "http://127.0.0.1:18069",
        })
    elif task == "lint":
        run(python(), "-m", "ruff", "check", "src", "tests", cwd=CORE)
    elif task == "urls":
        print(URLS)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "help")
