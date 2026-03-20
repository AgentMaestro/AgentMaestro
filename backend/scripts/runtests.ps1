param(
  [Parameter(ValueFromRemainingArguments=$true)]
  [string[]]$Args
)


$tempRoot = "C:\Dev\AgentMaestro\backend\pytest_temp"
$baseTemp = $tempRoot

New-Item -ItemType Directory -Force $tempRoot | Out-Null

# Pre-delete so pytest doesn't have to
if (Test-Path $baseTemp) { Remove-Item -Recurse -Force $baseTemp -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force $baseTemp | Out-Null

# ------------------------------------------------------------------------------------------------------

# Pre-delete workspace test path if it exists
$testPath = "C:\tmp\agentmaestro\sandbox\pytest_temp"
if (Test-Path $testPath) { Remove-Item -Recurse -Force $testPath -ErrorAction SilentlyContinue }

# Pre-delete other test folders
$test1Path = "C:\Dev\AgentMaestro\backend\.pytest-temp"
if (Test-Path $test1Path) { Remove-Item -Recurse -Force $test1Path -ErrorAction SilentlyContinue }

$test2Path = "C:\Dev\AgentMaestro\backend\DevAgentMaestrobackendpytest_temp"
if (Test-Path $test2Path) { Remove-Item -Recurse -Force $test2Path -ErrorAction SilentlyContinue }

# -------------------------------------------------------------------------------------------------------

$env:TMP = $tempRoot
$env:TEMP = $tempRoot
$env:PYTEST_ADDOPTS = "--basetemp=$baseTemp --ignore-glob=**\pytest_* --ignore-glob=**\.pytest_* --ignore-glob=**\pytest-*"
$env:DJANGO_ALLOW_ASYNC_UNSAFE = "1"  # workaround for async test issues to allow the legacy synchronous helpts to touch the ORM from within async tests

$previousTransport = $env:OPENAI_TRANSPORT
$env:OPENAI_TRANSPORT = "http"


# these skip this test in the tests, as they are quite long and exercise all the tools
# really only needs to be run/set to "1" if we change
# - llm.services.toolrunner_bridge
# - change ToolRunner routing, sandboxing or tool registration
# - want a true end-to-end verification that backend calls can reach ToolRunner and execute real tools
$env:TOOLRUNNER_BRIDGE_TEST="0"

try {
  Push-Location "C:\Dev\AgentMaestro\backend"
  .\.venv\Scripts\python -m pytest @Args
}
finally {
  Pop-Location
  if ($previousTransport) {
    $env:OPENAI_TRANSPORT = $previousTransport
  }
  else {
    Remove-Item Env:OPENAI_TRANSPORT -ErrorAction SilentlyContinue
  }
}

 $cleanupScript = @'
import os
import dj_database_url
import psycopg
from psycopg import sql

db_url = os.getenv("DATABASE_URL") or "postgresql://agentmaestro:agentmaestro@localhost:5432/agentmaestro"
cfg = dj_database_url.parse(db_url)
drop_name = "test_agentmaestro"
conn_params = {
    "host": cfg.get("HOST") or "localhost",
    "port": cfg.get("PORT") or 5432,
    "user": cfg.get("USER"),
    "password": cfg.get("PASSWORD"),
    "dbname": "postgres",
}
try:
    with psycopg.connect(**conn_params, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (drop_name,))
            if cur.fetchone():
                cur.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(drop_name))
                )
except Exception as exc:
    print(f"Unable to drop test database {drop_name}: {exc}")
'@

 $cleanupScript | .\.venv\Scripts\python -
