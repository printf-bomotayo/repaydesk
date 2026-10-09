"""Developer tasks that work the same on Windows, macOS and Linux. `make <task>` calls these.

    python scripts/dev.py env       create .env, or add keys .env.example has gained (random secrets)
    python scripts/dev.py up        start the stack and seed it (first run: 10 to 15 minutes)
    python scripts/dev.py up-langfuse   also start self-hosted Langfuse (about 2 GB more)
    python scripts/dev.py seed      seed again (skips what exists)
    python scripts/dev.py down      stop, keep data
    python scripts/dev.py reset     stop and delete all data, including the week's files
    python scripts/dev.py ingest    take in the week's statement files, then deliver its webhooks to the API
    python scripts/dev.py match     propose matches for new lines by rule (certain cases only)
    python scripts/dev.py score     score the live proposals against the truth file
    python scripts/dev.py directory copy the book from Fineract into the borrower directory now
    python scripts/dev.py gateway   the posting gateway's tests (.NET; database tests use the compose Postgres)
    python scripts/dev.py test      unit, contract and database tests (database tests skip without the stack)
    python scripts/dev.py itest     contract tests against the running Fineract and Odoo
    python scripts/dev.py lint      ruff, and the web app's type check
    python scripts/dev.py web       the web app with live reload (http://127.0.0.1:5173)
    python scripts/dev.py webtest   the web app's tests
    python scripts/dev.py urls      where everything is
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "services" / "core"
WEB = ROOT / "services" / "web"
GATEWAY = ROOT / "services" / "gateway"
URLS = """
  Fineract API   http://127.0.0.1:18080/fineract-provider/api/v1   (tenant: default, user from .env)
  Fineract UI    http://127.0.0.1:18081                             (Mifos X web app, same login)
  Odoo ledger    http://127.0.0.1:18069                             (database: ledger, user from .env)
  Jaeger         http://127.0.0.1:16686
  Repaydesk      http://127.0.0.1:18082                             (sign in as amaka, kunle, tunde or hadiza; STAFF_DEMO_PASSWORD in .env)
  API            http://127.0.0.1:18000/docs
  Postgres       127.0.0.1:15432
  Week's files   data/inbox/   (truth for evals: data/truth/)
"""


def run(*cmd: str, cwd: Path = ROOT, env: dict | None = None) -> None:
    print("$", " ".join(cmd), flush=True)
    code = subprocess.call(cmd, cwd=cwd, env={**os.environ, **(env or {})})
    if code:
        sys.exit(code)


def npm() -> str:
    return shutil.which("npm") or "npm"  # npm.cmd on Windows


def python() -> str:
    for candidate in [CORE / ".venv" / "Scripts" / "python.exe", CORE / ".venv" / "bin" / "python"]:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def new_secret() -> str:
    """URL-safe, so it can sit in a database URL, and strong enough for Fineract's password rule:
    upper and lower case, a digit, a symbol, no character twice in a row."""
    import re
    import secrets

    while True:
        s = secrets.token_urlsafe(18)
        if re.fullmatch(r"(?!.*(.)\1)(?=.*\d)(?=.*[a-z])(?=.*[A-Z])(?=.*-).{12,50}", s):
            return s


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
        value = re.sub(r"change-me-[a-z]+", lambda _: new_secret(), value)
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


def test_database_url() -> str:
    env = ROOT / ".env"
    if not env.exists():
        return ""
    values = dict(line.split("=", 1) for line in env.read_text().splitlines() if "=" in line and not line.startswith("#"))
    return f"postgresql://repaydesk:{values.get('REPAYDESK_DB_PASSWORD', '')}@127.0.0.1:15432/repaydesk"


def ensure_data_dir() -> None:
    # Docker would create the bind-mounted folder as root on Linux; the containers run as non-root.
    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    if os.name != "nt":
        data.chmod(0o777)


def main(task: str) -> None:
    if task == "env":
        ensure_env()
    elif task == "up":
        ensure_env()
        ensure_data_dir()
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
    elif task == "ingest":
        run("docker", "compose", "run", "--rm", "--build", "ingest")
        run("docker", "compose", "run", "--rm", "ingest", "python", "-m", "repaydesk.ingest",
            "--replay-webhooks", "http://api:8000")
    elif task == "match":
        run("docker", "compose", "run", "--rm", "--build", "match")
    elif task == "directory":
        # The directory service does this every 15 minutes; this is for when you can't wait.
        run("docker", "compose", "run", "--rm", "--build", "directory", "python", "-m", "repaydesk.directory")
    elif task == "score":
        # Proposals against the truth file; exits non-zero if any proposal is wrong.
        run("docker", "compose", "run", "--rm", "match", "python", "-m", "repaydesk.scoring", *sys.argv[2:])
    elif task == "test":
        # Database tests use the compose Postgres when it is up; without it they skip.
        run(python(), "-m", "pytest", "-q", cwd=CORE, env={"REPAYDESK_TEST_DATABASE_URL": test_database_url()})
    elif task == "gateway":
        run("dotnet", "test", str(GATEWAY), env={"REPAYDESK_TEST_DATABASE_URL": test_database_url()})
    elif task == "itest":
        run(python(), "-m", "pytest", "-q", "-m", "integration", cwd=CORE, env={
            "FINERACT_URL": "http://127.0.0.1:18080/fineract-provider/api/v1",
            "ODOO_URL": "http://127.0.0.1:18069",
        })
    elif task == "lint":
        run(python(), "-m", "ruff", "check", "src", "tests", cwd=CORE)
        run(npm(), "run", "typecheck", cwd=WEB)
    elif task == "web":
        # The web app with live reload on http://127.0.0.1:5173, against the API on 18000.
        if not (WEB / "node_modules").exists():
            run(npm(), "ci", "--no-audit", "--no-fund", cwd=WEB)
        run(npm(), "run", "dev", "--", "--host", "127.0.0.1", cwd=WEB)
    elif task == "webtest":
        run(npm(), "test", cwd=WEB)
    elif task == "urls":
        print(URLS)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "help")
