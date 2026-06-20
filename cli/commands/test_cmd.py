"""Test runner commands — unit tests, evals, token usage, banned-pattern checks."""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
import typer

from cli.config import BACKEND_DIR, FRONTEND_DIR, GRAMJS_SIDECAR_DIR, PORTS, PROJECT_ROOT
from cli.harness_env import build_backend_pytest_env, make_harness_db_suffix
from cli.output import header, status_line, table
from cli.runtime_zero import (
    DEFAULT_BACKEND_URL,
    DEFAULT_SIDECAR_URL,
    dumps_result,
    env_value,
    fetch_backend_health,
    fetch_sidecar_sessions,
    load_dotenv,
    run_runtime_zero_sync,
)

app = typer.Typer(no_args_is_help=True)


# ── Helpers ───────────────────────────────────────────────────────────────────


class Service(str, Enum):
    backend = "backend"
    frontend = "frontend"
    sidecar = "sidecar"
    all = "all"


def _venv_python(directory: "Path") -> str:  # noqa: F821
    """Return the venv python path for a service directory."""
    candidates = (
        directory / ".venv" / "bin" / "python",
        directory / "venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


def _run_backend_tests() -> dict:
    """Run backend pytest suite. Returns result dict."""
    result = subprocess.run(
        [_venv_python(BACKEND_DIR), "-m", "pytest", "tests/", "-x", "--tb=short", "-q"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    passed = result.returncode == 0
    # Parse summary line from pytest output (e.g. "12 passed, 2 failed in 3.4s")
    summary = _parse_pytest_summary(result.stdout + result.stderr)
    return {
        "service": "backend",
        "passed": passed,
        "summary": summary,
        "output": result.stdout + result.stderr,
    }


def _run_frontend_tests() -> dict:
    """Run frontend vitest suite. Returns result dict."""
    result = subprocess.run(
        ["npx", "vitest", "run"],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
    )
    passed = result.returncode == 0
    summary = _parse_vitest_summary(result.stdout + result.stderr)
    return {
        "service": "frontend",
        "passed": passed,
        "summary": summary,
        "output": result.stdout + result.stderr,
    }


def _run_sidecar_tests() -> dict:
    """Run GramJS sidecar node:test suite. Returns result dict."""
    result = subprocess.run(
        ["node", "--test"],
        cwd=GRAMJS_SIDECAR_DIR,
        capture_output=True,
        text=True,
    )
    passed = result.returncode == 0
    summary = _parse_node_test_summary(result.stdout + result.stderr)
    return {
        "service": "sidecar",
        "passed": passed,
        "summary": summary,
        "output": result.stdout + result.stderr,
    }


def _run_runtime_zero_browser_cache_proof() -> dict:
    """Run the Playwright proof that stale browser state is removable."""
    result = subprocess.run(
        ["npm", "run", "test:e2e:runtime-zero", "--", "--reporter=list"],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0
    summary = _parse_playwright_summary(output)
    return {
        "name": "browser_cache_reset",
        "passed": passed,
        "detail": (
            "browser origin state is cleared"
            if passed
            else "browser origin state proof failed"
        ),
        "data": {
            "summary": summary,
            "output": _tail_output(output),
        },
    }


def _run_app_capability_smoke(*, keep_fixture: bool = False) -> dict:
    """Run a real-browser smoke against live backend data and auth."""
    return _run_browser_fixture_smoke(
        seed_args=[],
        npm_script="test:e2e:app-capability",
        keep_fixture=keep_fixture,
        include_fake_telegram=False,
    )


def _read_sidecar_key() -> str:
    result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-c",
            "from app.core.config import get_settings; print(get_settings().sidecar_api_key)",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _run_fake_telegram_smoke(*, keep_fixture: bool = False) -> dict:
    """Run a real-browser smoke that injects a GramJS-shaped webhook event."""
    return _run_browser_fixture_smoke(
        seed_args=["--telegram-connected"],
        npm_script="test:e2e:fake-telegram",
        keep_fixture=keep_fixture,
        include_fake_telegram=True,
    )


def _sidecar_cleanup_summary(runtime_zero_result: dict) -> dict:
    sidecar_check = next(
        (
            check
            for check in runtime_zero_result.get("checks", [])
            if check.get("name") == "sidecar_stale_workspaces"
        ),
        None,
    )
    return {
        "passed": bool(sidecar_check and sidecar_check.get("passed")),
        "reset": runtime_zero_result.get("reset", {}),
        "check": sidecar_check,
    }


def _run_admin_runtime_smoke(*, keep_fixture: bool = False) -> dict:
    """Run a real-browser smoke against the founder runtime console."""
    if keep_fixture:
        return {
            "passed": False,
            "skipped": False,
            "summary": "--keep-fixture is not supported for founder runtime smoke",
            "fixture": None,
            "sidecar_cleanup": None,
            "output": (
                "Founder runtime smoke uses an existing ADMIN_WORKSPACE_IDS "
                "allowlisted account from OQIM_ADMIN_SMOKE_* env vars. It does "
                "not seed a dynamic founder workspace."
            ),
        }

    phone = os.environ.get("OQIM_ADMIN_SMOKE_PHONE")
    password = os.environ.get("OQIM_ADMIN_SMOKE_PASSWORD")
    if not phone or not password:
        return {
            "passed": True,
            "skipped": True,
            "summary": (
                "skipped: set OQIM_ADMIN_SMOKE_PHONE/OQIM_ADMIN_SMOKE_PASSWORD "
                "for an ADMIN_WORKSPACE_IDS allowlisted founder account"
            ),
            "fixture": None,
            "sidecar_cleanup": None,
            "output": "",
        }

    result = subprocess.run(
        ["npm", "run", "test:e2e:admin-runtime", "--", "--reporter=list"],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    output = result.stdout + result.stderr
    return {
        "passed": result.returncode == 0,
        "skipped": False,
        "summary": _parse_playwright_summary(output),
        "fixture": None,
        "sidecar_cleanup": None,
        "output": output,
    }


def _run_full_browser_smoke() -> dict:
    """Run seller-side browser proofs first, then admin-side proof."""
    steps = [
        {"name": "seller-app", "result": _run_app_capability_smoke()},
        {"name": "seller-fake-telegram", "result": _run_fake_telegram_smoke()},
        {"name": "admin-runtime", "result": _run_admin_runtime_smoke()},
    ]
    passed = all(step["result"].get("passed") for step in steps)
    skipped = sum(1 for step in steps if step["result"].get("skipped"))
    passable_steps = len(steps) - skipped
    passed_steps = sum(
        1 for step in steps
        if step["result"].get("passed") and not step["result"].get("skipped")
    )
    return {
        "passed": passed,
        "summary": f"{passed_steps}/{passable_steps} browser smokes passed, {skipped} skipped",
        "steps": steps,
    }


def _seed_app_fixture(*, telegram_connected: bool = False, admin: bool = False) -> dict:
    seed_args: list[str] = []
    if telegram_connected:
        seed_args.append("--telegram-connected")
    if admin:
        seed_args.append("--admin")
    seed = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "scripts/app_capability_fixture.py",
            "seed",
            *seed_args,
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    if seed.returncode != 0:
        raise RuntimeError(seed.stdout + seed.stderr)
    try:
        return json.loads(seed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise RuntimeError(f"fixture seed produced invalid JSON: {seed.stdout}{seed.stderr}") from exc


def _cleanup_app_fixture(workspace_id: int) -> dict:
    cleanup = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "scripts/app_capability_fixture.py",
            "cleanup",
            "--workspace-id",
            str(workspace_id),
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    if cleanup.returncode != 0:
        raise RuntimeError(cleanup.stdout + cleanup.stderr)
    try:
        return json.loads(cleanup.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise RuntimeError(f"fixture cleanup produced invalid JSON: {cleanup.stdout}{cleanup.stderr}") from exc


def _run_browser_fixture_smoke(
    *,
    seed_args: list[str],
    npm_script: str,
    keep_fixture: bool,
    include_fake_telegram: bool,
    admin_env: bool = False,
) -> dict:
    """Seed a browser fixture, run a frontend E2E script, and clean it up."""
    seed = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "scripts/app_capability_fixture.py",
            "seed",
            *seed_args,
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    seed_output = seed.stdout + seed.stderr
    if seed.returncode != 0:
        return {
            "passed": False,
            "summary": "fixture seed failed",
            "fixture": None,
            "output": seed_output,
        }

    try:
        fixture = json.loads(seed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        return {
            "passed": False,
            "summary": "fixture seed produced invalid JSON",
            "fixture": None,
            "output": f"{seed_output}\n{exc}",
        }

    env = {
        **os.environ,
        "OQIM_SMOKE_PHONE": fixture["phone"],
        "OQIM_SMOKE_PASSWORD": fixture["password"],
        "OQIM_SMOKE_CONVERSATION_ID": str(fixture["conversation_id"]),
        "OQIM_SMOKE_CUSTOMER_NAME": fixture["customer_name"],
        "OQIM_SMOKE_FIRST_MESSAGE": fixture["first_message"],
        "OQIM_SMOKE_LATEST_MESSAGE": fixture["latest_message"],
    }
    if include_fake_telegram:
        env["OQIM_SMOKE_TELEGRAM_USER_ID"] = str(fixture.get("telegram_user_id") or "")
        env["OQIM_SMOKE_SIDECAR_KEY"] = _read_sidecar_key()
    if admin_env:
        env["OQIM_ADMIN_SMOKE_PHONE"] = fixture["phone"]
        env["OQIM_ADMIN_SMOKE_PASSWORD"] = fixture["password"]
    result = subprocess.run(
        ["npm", "run", npm_script, "--", "--reporter=list"],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
        env=env,
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0

    cleanup_output = ""
    sidecar_cleanup: dict | None = None
    if passed and not keep_fixture:
        cleanup = subprocess.run(
            [
                _venv_python(BACKEND_DIR),
                "scripts/app_capability_fixture.py",
                "cleanup",
                "--workspace-id",
                str(fixture["workspace_id"]),
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
        )
        cleanup_output = cleanup.stdout + cleanup.stderr
        if cleanup.returncode != 0:
            passed = False
            output += "\n--- fixture cleanup failed ---\n" + cleanup_output
        else:
            sidecar_cleanup = _sidecar_cleanup_summary(
                run_runtime_zero_sync(cleanup_sidecar=True)
            )
            if not sidecar_cleanup["passed"]:
                passed = False
                output += (
                    "\n--- fixture sidecar cleanup failed ---\n"
                    + json.dumps(sidecar_cleanup, indent=2)
                )

    return {
        "passed": passed,
        "summary": _parse_playwright_summary(output),
        "fixture": {
            "workspace_id": fixture["workspace_id"],
            "conversation_id": fixture["conversation_id"],
            "kept": keep_fixture or not passed,
        },
        "sidecar_cleanup": sidecar_cleanup,
        "output": output,
    }


def _run_dependency_truth_check() -> dict:
    """Check live backend and sidecar dependencies without requiring empty local state."""

    async def _check() -> dict:
        dotenv = load_dotenv()
        backend = env_value("BACKEND_URL", DEFAULT_BACKEND_URL, dotenv)
        sidecar = env_value("SIDECAR_URL", DEFAULT_SIDECAR_URL, dotenv)
        sidecar_key = env_value("SIDECAR_API_KEY", "dev-sidecar-key", dotenv)
        checks: list[dict] = []

        try:
            health = await fetch_backend_health(backend)
            backend_ok = (
                health.get("status") == "ok"
                and health.get("database") == "connected"
                and health.get("redis") == "connected"
            )
            checks.append({
                "name": "backend_health",
                "passed": backend_ok,
                "detail": "backend, Postgres, and Redis are healthy"
                if backend_ok
                else "backend health is degraded",
                "data": health,
            })
        except Exception as exc:
            checks.append({
                "name": "backend_health",
                "passed": False,
                "detail": str(exc),
                "data": {},
            })

        try:
            sessions = await fetch_sidecar_sessions(sidecar, sidecar_key)
            checks.append({
                "name": "sidecar_sessions",
                "passed": True,
                "detail": "sidecar is reachable",
                "data": {"session_count": len(sessions)},
            })
        except Exception as exc:
            checks.append({
                "name": "sidecar_sessions",
                "passed": False,
                "detail": str(exc),
                "data": {},
            })

        return {
            "passed": all(check["passed"] for check in checks),
            "summary": f"{sum(1 for check in checks if check['passed'])}/{len(checks)} dependency checks passed",
            "checks": checks,
        }

    return asyncio.run(_check())


def _run_api_capability_smoke(*, keep_fixture: bool = False) -> dict:
    """Seed a small workspace and prove core APIs read live canonical data."""

    async def _check(fixture: dict) -> dict:
        dotenv = load_dotenv()
        backend = env_value("BACKEND_URL", DEFAULT_BACKEND_URL, dotenv).rstrip("/")
        checks: list[dict] = []
        async with httpx.AsyncClient(base_url=backend, timeout=10.0) as client:
            login = await client.post(
                "/api/auth/login",
                json={
                    "phone_number": fixture["phone"],
                    "password": fixture["password"],
                },
            )
            checks.append({
                "name": "auth_login",
                "passed": login.status_code == 200,
                "detail": f"status={login.status_code}",
            })
            if login.status_code != 200:
                return checks

            conversations = await client.get("/api/conversations", params={"limit": 50})
            conversations_json = conversations.json() if conversations.status_code == 200 else {}
            items = conversations_json.get("items", []) if isinstance(conversations_json, dict) else []
            latest_ok = any(
                item.get("id") == fixture["conversation_id"]
                and item.get("last_message_text") == fixture["latest_message"]
                for item in items
                if isinstance(item, dict)
            )
            checks.append({
                "name": "conversations_projection",
                "passed": conversations.status_code == 200 and latest_ok,
                "detail": f"status={conversations.status_code}, latest_ok={latest_ok}",
            })

            detail = await client.get(f"/api/conversations/{fixture['conversation_id']}")
            detail_json = detail.json() if detail.status_code == 200 else {}
            checks.append({
                "name": "conversation_detail",
                "passed": detail.status_code == 200
                and isinstance(detail_json, dict)
                and detail_json.get("id") == fixture["conversation_id"],
                "detail": f"status={detail.status_code}",
            })

            messages = await client.get(
                f"/api/conversations/{fixture['conversation_id']}/messages",
                params={"limit": 50},
            )
            messages_json = messages.json() if messages.status_code == 200 else {}
            message_items = messages_json.get("items", []) if isinstance(messages_json, dict) else []
            message_texts = [item.get("content") for item in message_items if isinstance(item, dict)]
            messages_ok = fixture["first_message"] in message_texts and fixture["latest_message"] in message_texts
            checks.append({
                "name": "message_tail",
                "passed": messages.status_code == 200 and messages_ok,
                "detail": f"status={messages.status_code}, tail_ok={messages_ok}",
            })

            reply_inbox = await client.get("/api/ai-replies", params={"status": "draft"})
            checks.append({
                "name": "reply_inbox",
                "passed": reply_inbox.status_code == 200 and isinstance(reply_inbox.json(), list),
                "detail": f"status={reply_inbox.status_code}",
            })

            conversation_replies = await client.get(f"/api/conversations/{fixture['conversation_id']}/ai-replies")
            checks.append({
                "name": "conversation_replies",
                "passed": conversation_replies.status_code == 200 and isinstance(conversation_replies.json(), list),
                "detail": f"status={conversation_replies.status_code}",
            })

            dashboard = await client.get("/api/bi-promoter/analytics/dashboard")
            dashboard_json = dashboard.json() if dashboard.status_code == 200 else {}
            checks.append({
                "name": "seller_analytics_dashboard",
                "passed": dashboard.status_code == 200
                and isinstance(dashboard_json, dict)
                and dashboard_json.get("schema_version") == "bi_analytics_dashboard.v1",
                "detail": f"status={dashboard.status_code}",
            })

            media = await client.get("/api/media/0/0", params={"thumb": "true"})
            checks.append({
                "name": "media_route",
                "passed": media.status_code in {404, 422},
                "detail": f"status={media.status_code} (expected missing media, route reachable)",
            })

        return checks

    fixture: dict | None = None
    output = ""
    try:
        fixture = _seed_app_fixture()
        checks = asyncio.run(_check(fixture))
        passed = all(check["passed"] for check in checks)
    except Exception as exc:
        checks = [{"name": "api_capability", "passed": False, "detail": str(exc)}]
        passed = False
        output = str(exc)

    cleanup_output = ""
    if fixture and passed and not keep_fixture:
        try:
            cleanup_output = json.dumps(_cleanup_app_fixture(fixture["workspace_id"]), sort_keys=True)
            sidecar_cleanup = _sidecar_cleanup_summary(
                run_runtime_zero_sync(cleanup_sidecar=True)
            )
            if not sidecar_cleanup["passed"]:
                passed = False
                output += "\nsidecar cleanup failed: " + json.dumps(sidecar_cleanup, indent=2)
        except Exception as exc:
            passed = False
            output += f"\nfixture cleanup failed: {exc}"

    return {
        "passed": passed,
        "summary": f"{sum(1 for check in checks if check['passed'])}/{len(checks)} API checks passed",
        "fixture": None if fixture is None else {
            "workspace_id": fixture["workspace_id"],
            "conversation_id": fixture["conversation_id"],
            "kept": keep_fixture or not passed,
        },
        "checks": checks,
        "cleanup": cleanup_output,
        "output": output,
    }


def _last_non_empty_message_text(message_items: list[dict]) -> str | None:
    for item in reversed(message_items):
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None


def _browser_text_probe(value: str | None) -> str | None:
    if not value:
        return None
    collapsed = " ".join(value.split())
    if not collapsed:
        return None
    return collapsed[:120]


def _run_live_chat_truth_smoke(
    *,
    workspace_id: int,
    conversation_id: int | None = None,
    scan_limit: int = 20,
) -> dict:
    """Open a real local conversation in the browser and prove canonical messages render."""

    async def _load_fixture() -> tuple[dict | None, list[dict], str]:
        dotenv = load_dotenv()
        backend = env_value("BACKEND_URL", DEFAULT_BACKEND_URL, dotenv).rstrip("/")
        checks: list[dict] = []
        async with httpx.AsyncClient(base_url=backend, timeout=15.0) as client:
            login = await client.get(f"/api/auth/dev-login/{workspace_id}")
            checks.append({
                "name": "dev_login",
                "passed": login.status_code in {200, 302, 303},
                "detail": f"status={login.status_code}",
            })
            if login.status_code not in {200, 302, 303}:
                return None, checks, login.text

            conversations = await client.get("/api/conversations", params={"limit": scan_limit})
            conversations_json = conversations.json() if conversations.status_code == 200 else {}
            items = conversations_json.get("items", []) if isinstance(conversations_json, dict) else []
            checks.append({
                "name": "conversation_list",
                "passed": conversations.status_code == 200 and bool(items),
                "detail": f"status={conversations.status_code}, count={len(items)}",
            })
            if conversations.status_code != 200 or not items:
                return None, checks, conversations.text

            candidate_ids = [conversation_id] if conversation_id is not None else [
                int(item["id"])
                for item in items
                if isinstance(item, dict) and isinstance(item.get("id"), int)
            ]
            for candidate_id in candidate_ids:
                detail = await client.get(f"/api/conversations/{candidate_id}")
                messages = await client.get(
                    f"/api/conversations/{candidate_id}/messages",
                    params={"limit": 50},
                )
                detail_json = detail.json() if detail.status_code == 200 else {}
                messages_json = messages.json() if messages.status_code == 200 else {}
                message_items = messages_json.get("items", []) if isinstance(messages_json, dict) else []
                latest_preview = detail_json.get("last_message_text") if isinstance(detail_json, dict) else None
                expected_text = _last_non_empty_message_text(message_items)
                fixture_ok = (
                    detail.status_code == 200
                    and messages.status_code == 200
                    and isinstance(detail_json, dict)
                    and bool(message_items)
                    and (bool(expected_text) or bool(latest_preview))
                )
                if fixture_ok:
                    checks.append({
                        "name": "message_tail",
                        "passed": True,
                        "detail": (
                            f"conversation={candidate_id}, messages={len(message_items)}, "
                            f"latest={_browser_text_probe(latest_preview)!r}"
                        ),
                    })
                    return {
                        "workspace_id": workspace_id,
                        "conversation_id": candidate_id,
                        "customer_name": detail_json.get("customer_name"),
                        "expected_text": _browser_text_probe(expected_text),
                        "latest_preview": _browser_text_probe(latest_preview),
                        "message_count": len(message_items),
                    }, checks, ""

                checks.append({
                    "name": f"candidate_{candidate_id}",
                    "passed": False,
                    "detail": (
                        f"detail={detail.status_code}, messages={messages.status_code}, "
                        f"count={len(message_items)}, latest={_browser_text_probe(latest_preview)!r}"
                    ),
                })

        return None, checks, "No candidate conversation had renderable canonical messages."

    fixture, checks, output = asyncio.run(_load_fixture())
    if fixture is None:
        return {
            "passed": False,
            "summary": f"{sum(1 for check in checks if check['passed'])}/{len(checks)} API checks passed",
            "fixture": None,
            "checks": checks,
            "output": output,
        }

    dotenv = load_dotenv()
    backend = env_value("BACKEND_URL", DEFAULT_BACKEND_URL, dotenv).rstrip("/")
    frontend = env_value("FRONTEND_URL", f"http://localhost:{PORTS['frontend']}", dotenv).rstrip("/")
    script = """
const { chromium } = await import('playwright')

const config = JSON.parse(process.env.OQIM_LIVE_CHAT_PROOF || '{}')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1728, height: 1117 } })
const responses = []
page.on('response', async (res) => {
  const url = res.url()
  if (url.includes('/api/auth/session') || url.includes(`/api/conversations/${config.conversation_id}`) || url.endsWith('/api/conversations?limit=50')) {
    let body = ''
    try {
      body = (await res.text()).slice(0, 200).replace(/\\s+/g, ' ')
    } catch {}
    responses.push({ status: res.status(), url, body })
  }
})

await page.goto(`${config.backend}/api/auth/dev-login/${config.workspace_id}`, { waitUntil: 'domcontentloaded' })
await page.goto(`${config.frontend}/conversations/${config.conversation_id}`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(3500)
const body = await page.textContent('body')
const normalizedBody = body.replace(/\\s+/g, ' ').trim()
const expectedText = config.expected_text || ''
const latestPreview = config.latest_preview || ''
const hasExpectedText = expectedText ? normalizedBody.includes(expectedText) : true
const hasLatestPreview = latestPreview ? normalizedBody.includes(latestPreview) : true
const empty = body.includes("Xabar yo'q")
const loading = body.includes('Telegramdan xabarlar yuklanmoqda')
const networkError = body.includes('Tarmoq xatosi') || body.includes('Xizmat vaqtincha ishlamayapti')
const ok = hasExpectedText && hasLatestPreview && !empty && !loading && !networkError
const result = {
  ok,
  url: page.url(),
  hasExpectedText,
  hasLatestPreview,
  empty,
  loading,
  networkError,
  expectedText,
  latestPreview,
  bodySnippet: body.slice(0, 1200),
  responses,
}
console.log(JSON.stringify(result, null, 2))
await page.screenshot({ path: '/tmp/oqim-live-chat-truth.png', fullPage: true })
await browser.close()
process.exit(ok ? 0 : 1)
"""
    env = {
        **os.environ,
        "OQIM_LIVE_CHAT_PROOF": json.dumps({
            **fixture,
            "backend": backend,
            "frontend": frontend,
        }),
    }
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
        env=env,
    )
    browser_output = result.stdout + result.stderr
    browser_json: dict | None = None
    try:
        browser_json = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        browser_json = None
    browser_passed = result.returncode == 0 and bool(browser_json and browser_json.get("ok"))
    checks.append({
        "name": "browser_chat_render",
        "passed": browser_passed,
        "detail": (
            "browser rendered canonical message tail"
            if browser_passed
            else "browser did not render canonical message tail"
        ),
        "data": browser_json,
    })

    return {
        "passed": all(check["passed"] for check in checks),
        "summary": f"{sum(1 for check in checks if check['passed'])}/{len(checks)} live chat checks passed",
        "fixture": fixture,
        "checks": checks,
        "output": browser_output,
        "screenshot": "/tmp/oqim-live-chat-truth.png",
    }


def _run_replay_harness() -> dict:
    """Run the canonical replay harness."""
    result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            "tests/test_replay_harness.py",
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0
    return {
        "passed": passed,
        "summary": _parse_pytest_summary(output),
        "output": output,
    }


def _run_conversation_tail_harness() -> dict:
    """Run the conversation tail projection/gap harness."""
    result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            "tests/test_conversation_tail_harness.py",
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0
    return {
        "passed": passed,
        "summary": _parse_pytest_summary(output),
        "output": output,
    }


def _run_delivery_chaos_harness() -> dict:
    """Run the delivery idempotency/unknown/reclaim chaos harness."""
    result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            "tests/test_delivery_chaos_harness.py",
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0
    return {
        "passed": passed,
        "summary": _parse_pytest_summary(output),
        "output": output,
    }


def _run_media_chaos_harness() -> dict:
    """Run the media state/action/streaming chaos harness."""
    result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            "tests/test_media_chaos_harness.py",
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0
    return {
        "passed": passed,
        "summary": _parse_pytest_summary(output),
        "output": output,
    }


def _run_reconnect_harness() -> dict:
    """Run backend/frontend reconnect equivalence proofs."""
    backend_result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            "tests/test_sync_session.py",
            "tests/test_sync.py::TestSyncSession",
            "tests/test_websocket.py::TestWebSocketEndpoint::test_ws_session_resume_returns_new_session_delta_event",
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    frontend_result = subprocess.run(
        [
            "npx",
            "vitest",
            "run",
            "src/lib/sync-session.test.ts",
            "src/lib/active-tail-sync.test.ts",
            "src/lib/sync-projections.test.ts",
            "src/hooks/use-websocket.test.ts",
            "src/hooks/conversation-runtime.boundary.test.ts",
        ],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
    )
    backend_output = backend_result.stdout + backend_result.stderr
    frontend_output = frontend_result.stdout + frontend_result.stderr
    passed = backend_result.returncode == 0 and frontend_result.returncode == 0
    backend_summary = _parse_pytest_summary(backend_output)
    frontend_summary = _parse_vitest_summary(frontend_output)
    return {
        "passed": passed,
        "summary": f"backend: {backend_summary}; frontend: {frontend_summary}",
        "output": "\n--- backend ---\n"
        + backend_output
        + "\n--- frontend ---\n"
        + frontend_output,
    }


def _run_onboarding_chaos_harness() -> dict:
    """Run Telegram onboarding/session recoverability proofs."""
    backend_result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            "tests/test_telegram_connection_state.py",
            "tests/test_telegram_auth.py::TestTelegramPhoneAuth",
            "tests/test_telegram_auth.py::TestTelegramOnboardingCompat",
            "tests/test_onboarding.py",
            "tests/test_onboarding_ingestion.py",
            "tests/test_onboarding_runtime.py",
            "tests/test_onboarding_source_ingestion.py",
            "tests/test_onboarding_source_learning_runtime.py",
            "tests/test_vertex_gemini_gateway_phase75.py::test_llm_gateway_uploads_file_api_content_parts",
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    frontend_result = subprocess.run(
        [
            "npx",
            "vitest",
            "run",
            "src/components/blocks/onboarding/phone-auth.test.tsx",
            "src/hooks/use-telegram-connection-status.test.ts",
        ],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
    )
    sidecar_result = subprocess.run(
        [
            "node",
            "--test",
            "session-binding-policy.test.js",
            "runtime-registry.test.js",
            "sidecar-status.test.js",
        ],
        cwd=GRAMJS_SIDECAR_DIR,
        capture_output=True,
        text=True,
    )
    backend_output = backend_result.stdout + backend_result.stderr
    frontend_output = frontend_result.stdout + frontend_result.stderr
    sidecar_output = sidecar_result.stdout + sidecar_result.stderr
    passed = (
        backend_result.returncode == 0
        and frontend_result.returncode == 0
        and sidecar_result.returncode == 0
    )
    return {
        "passed": passed,
        "summary": (
            f"backend: {_parse_pytest_summary(backend_output)}; "
            f"frontend: {_parse_vitest_summary(frontend_output)}; "
            f"sidecar: {_parse_node_test_summary(sidecar_output)}"
        ),
        "output": "\n--- backend ---\n"
        + backend_output
        + "\n--- frontend ---\n"
        + frontend_output
        + "\n--- sidecar ---\n"
        + sidecar_output,
    }


def _run_embedding_chaos_harness() -> dict:
    """Run embedding/RAG degradation and tenant-isolation proofs."""
    result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            "tests/test_embedding_batch.py",
            "tests/test_embedding_chaos_harness.py",
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0
    return {
        "passed": passed,
        "summary": _parse_pytest_summary(output),
        "output": output,
    }


def _run_tenant_harness(*, workspaces: int) -> dict:
    """Run deterministic multi-tenant isolation, fairness, and noisy-neighbor proofs."""
    capped_workspaces = max(1, min(int(workspaces or 1000), 5000))
    proof_paths = [
        "tests/test_tenant_chaos_harness.py",
        "tests/test_turn_session_runner.py::test_lease_ready_turns_fairly_claims_one_turn_per_workspace_first",
        "tests/test_thousand_tenants.py",
    ]
    result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            *proof_paths,
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env={**os.environ, "OQIM_TENANT_HARNESS_WORKSPACES": str(capped_workspaces)},
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0
    return {
        "passed": passed,
        "summary": _parse_pytest_summary(output),
        "workspaces": capped_workspaces,
        "proof_paths": proof_paths,
        "output": output,
    }

def _run_adapter_contract_harness() -> dict:
    """Run channel adapter, persistence, and delivery-plan replay proofs."""
    proof_paths = [
        "tests/test_channel_adapter_contract.py",
        "tests/test_channel_agnostic_persistence.py",
        "tests/test_channel_delivery_eval.py",
    ]
    result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            *proof_paths,
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0
    return {
        "passed": passed,
        "summary": _parse_pytest_summary(output),
        "proof_paths": proof_paths,
        "output": output,
    }


def _run_telegram_intake_harness(
    *,
    include_browser: bool = False,
    keep_fixture: bool = False,
) -> dict:
    """Run the Telegram authoritative intake proof from sidecar shape to projections."""
    proof_paths = [
        "tests/test_telegram_intake_harness.py",
        "tests/api/test_webhook_publishes.py",
        "tests/test_channel_adapter_contract.py::test_channel_adapter_events_use_same_inbound_to_reply_path",
    ]
    backend_result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            *proof_paths,
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    sidecar_result = subprocess.run(
        ["node", "--test"],
        cwd=GRAMJS_SIDECAR_DIR,
        capture_output=True,
        text=True,
    )
    backend_output = backend_result.stdout + backend_result.stderr
    sidecar_output = sidecar_result.stdout + sidecar_result.stderr
    checks = [
        {
            "name": "backend_authoritative_intake",
            "passed": backend_result.returncode == 0,
            "summary": _parse_pytest_summary(backend_output),
            "proof_paths": proof_paths,
            "output": backend_output,
        },
        {
            "name": "gramjs_sidecar_contract",
            "passed": sidecar_result.returncode == 0,
            "summary": _parse_node_test_summary(sidecar_output),
            "output": sidecar_output,
        },
    ]
    if include_browser:
        browser_result = _run_fake_telegram_smoke(keep_fixture=keep_fixture)
        checks.append({
            "name": "browser_fake_telegram_smoke",
            "passed": browser_result.get("passed", False),
            "summary": browser_result.get("summary", "(no summary)"),
            "output": browser_result.get("output", ""),
            "result": browser_result,
        })

    passed = all(check["passed"] for check in checks)
    summary_parts = [f"{check['name']}: {check['summary']}" for check in checks]
    if not include_browser:
        summary_parts.append("browser_fake_telegram_smoke: skipped")
    return {
        "passed": passed,
        "summary": "; ".join(summary_parts),
        "checks": checks,
        "proof_paths": proof_paths,
        "browser_included": include_browser,
        "output": "\n--- backend ---\n"
        + backend_output
        + "\n--- sidecar ---\n"
        + sidecar_output
        + (
            "\n--- browser ---\n" + checks[-1].get("output", "")
            if include_browser and len(checks) > 2
            else ""
        ),
    }


def _live_telegram_headers(sidecar_key: str) -> dict[str, str]:
    return {"x-sidecar-key": sidecar_key} if sidecar_key else {}


def _select_live_telegram_session(
    sessions: list[dict],
    *,
    workspace_id: int | None,
) -> dict | None:
    if workspace_id:
        return next(
            (
                session
                for session in sessions
                if int(session.get("workspaceId") or 0) == workspace_id
            ),
            None,
        )
    connected = [
        session
        for session in sessions
        if session.get("state") == "connected" and int(session.get("workspaceId") or 0) > 0
    ]
    if connected:
        return connected[0]
    return next(
        (session for session in sessions if int(session.get("workspaceId") or 0) > 0),
        None,
    )


def _select_live_telegram_channel(
    channels: list[dict],
    *,
    channel: str | None,
) -> dict | None:
    if channel:
        wanted = channel.strip().lstrip("@").lower()
        return next(
            (
                item
                for item in channels
                if str(item.get("id") or "").lower() == wanted
                or str(item.get("username") or "").lstrip("@").lower() == wanted
                or str(item.get("handle") or "").lstrip("@").lower() == wanted
                or str(item.get("name") or "").lower() == wanted
            ),
            None,
        )
    own_channels = [item for item in channels if item.get("is_own")]
    if own_channels:
        return own_channels[0]
    return channels[0] if channels else None


def _live_telegram_onboarding_next_actions(
    *,
    workspace_id: int | None,
    sidecar_reachable: bool = False,
) -> list[str]:
    workspace_arg = f" --workspace-id {workspace_id}" if workspace_id else ""
    actions = [
        "Connect a real Telegram session in Onboarding or Settings.",
        f"Re-run: oqim test live-telegram-onboarding{workspace_arg} --json",
    ]
    if not sidecar_reachable:
        actions.insert(0, "Start the GramJS sidecar: oqim dev start --local")
    return actions


def _build_onboarding_telegram_source_item(
    *,
    selected_channel: dict,
    posts: list[dict],
) -> dict:
    channel_id = str(selected_channel.get("id") or selected_channel.get("username") or "").strip()
    label = str(selected_channel.get("name") or selected_channel.get("username") or channel_id or "Telegram channel")
    messages = [
        {
            "post_id": post.get("postId") or post.get("id") or post.get("messageId"),
            "date": post.get("date"),
            "text": post.get("text") or post.get("message") or "",
            "media": post.get("media"),
        }
        for post in posts
        if isinstance(post, dict) and (post.get("text") or post.get("message"))
    ]
    return {
        "kind": "telegram_channel",
        "label": label,
        "handle": selected_channel.get("username") or selected_channel.get("handle"),
        "channel_id": channel_id,
        "messages": messages,
    }


def _public_onboarding_source_item(source_item: dict | None) -> dict | None:
    if not source_item:
        return None
    messages = source_item.get("messages") or []
    return {
        "kind": source_item.get("kind"),
        "label": source_item.get("label"),
        "handle": source_item.get("handle"),
        "channel_id": source_item.get("channel_id"),
        "message_count": len(messages),
        "messages": [
            {
                "post_id": message.get("post_id"),
                "date": message.get("date"),
                "has_text": bool(message.get("text")),
                "has_media": bool(message.get("media")),
            }
            for message in messages
            if isinstance(message, dict)
        ],
    }


def _run_live_telegram_onboarding_probe(
    *,
    workspace_id: int | None = None,
    channel: str | None = None,
    limit: int = 30,
) -> dict:
    """Read live Telegram channel posts and prove they form an onboarding source payload."""

    async def _check() -> dict:
        dotenv = load_dotenv()
        sidecar = env_value("SIDECAR_URL", DEFAULT_SIDECAR_URL, dotenv).rstrip("/")
        sidecar_key = env_value("SIDECAR_API_KEY", "dev-sidecar-key", dotenv)
        headers = _live_telegram_headers(sidecar_key)
        checks: list[dict] = []
        selected_session: dict | None = None
        selected_channel: dict | None = None
        channels: list[dict] = []
        posts: list[dict] = []
        source_item: dict | None = None

        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                sessions = await fetch_sidecar_sessions(sidecar, sidecar_key)
                selected_session = _select_live_telegram_session(
                    sessions,
                    workspace_id=workspace_id,
                )
                checks.append({
                    "name": "sidecar_sessions",
                    "passed": selected_session is not None,
                    "detail": (
                        f"selected workspace {selected_session.get('workspaceId')}"
                        if selected_session
                        else "no workspace session found"
                    ),
                    "data": {
                        "session_count": len(sessions),
                        "workspace_id": selected_session.get("workspaceId") if selected_session else None,
                        "state": selected_session.get("state") if selected_session else None,
                    },
                })
            except Exception as exc:
                checks.append({
                    "name": "sidecar_sessions",
                    "passed": False,
                    "detail": str(exc),
                    "data": {},
                })
                return {
                    "passed": False,
                    "summary": "sidecar session discovery failed",
                    "checks": checks,
                    "source_item": None,
                }

            session_state = selected_session.get("state") if selected_session else None
            checks.append({
                "name": "telegram_connected",
                "passed": session_state == "connected",
                "detail": f"session state={session_state}",
                "data": {"workspace_id": selected_session.get("workspaceId") if selected_session else None},
            })
            if session_state != "connected":
                return {
                    "passed": False,
                    "summary": "selected Telegram workspace is not connected",
                    "checks": checks,
                    "source_item": None,
                    "blocked": True,
                    "blocker": "no_connected_telegram_sidecar_session",
                    "next_actions": _live_telegram_onboarding_next_actions(
                        workspace_id=workspace_id,
                        sidecar_reachable=True,
                    ),
                }

            selected_workspace_id = int(selected_session.get("workspaceId") or 0)
            try:
                response = await client.get(
                    f"{sidecar}/channels",
                    params={"workspaceId": selected_workspace_id},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
                channels = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
                selected_channel = _select_live_telegram_channel(channels, channel=channel)
                checks.append({
                    "name": "telegram_channels",
                    "passed": selected_channel is not None,
                    "detail": (
                        f"selected {selected_channel.get('name') or selected_channel.get('username') or selected_channel.get('id')}"
                        if selected_channel
                        else "no Telegram channel found"
                    ),
                    "data": {
                        "channel_count": len(channels),
                        "selected_channel_id": selected_channel.get("id") if selected_channel else None,
                        "selected_channel_username": selected_channel.get("username") if selected_channel else None,
                    },
                })
            except Exception as exc:
                checks.append({
                    "name": "telegram_channels",
                    "passed": False,
                    "detail": str(exc),
                    "data": {},
                })
                return {
                    "passed": False,
                    "summary": "channel discovery failed",
                    "checks": checks,
                    "source_item": None,
                }

            if not selected_channel:
                return {
                    "passed": False,
                    "summary": "no Telegram channel available for onboarding proof",
                    "checks": checks,
                    "source_item": None,
                    "blocked": True,
                    "blocker": "no_telegram_channel_available",
                    "next_actions": [
                        "Create or join a Telegram channel from the connected seller account.",
                        f"Re-run: oqim test live-telegram-onboarding --workspace-id {selected_workspace_id} --json",
                    ],
                }

            channel_ref = (
                selected_channel.get("username")
                or selected_channel.get("handle")
                or selected_channel.get("id")
            )
            try:
                response = await client.get(
                    f"{sidecar}/channel-posts",
                    params={
                        "workspaceId": selected_workspace_id,
                        "channelId": str(channel_ref),
                        "limit": max(1, min(limit, 300)),
                    },
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
                posts = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
                text_posts = [
                    post
                    for post in posts
                    if isinstance(post, dict) and (post.get("text") or post.get("message"))
                ]
                checks.append({
                    "name": "telegram_channel_posts",
                    "passed": len(text_posts) > 0,
                    "detail": f"{len(text_posts)} text posts fetched from {len(posts)} posts",
                    "data": {
                        "post_count": len(posts),
                        "text_post_count": len(text_posts),
                        "limit": limit,
                    },
                })
            except Exception as exc:
                checks.append({
                    "name": "telegram_channel_posts",
                    "passed": False,
                    "detail": str(exc),
                    "data": {},
                })
                return {
                    "passed": False,
                    "summary": "channel post fetch failed",
                    "checks": checks,
                    "source_item": None,
                }

        if selected_channel:
            source_item = _build_onboarding_telegram_source_item(
                selected_channel=selected_channel,
                posts=posts,
            )
            checks.append({
                "name": "onboarding_source_payload",
                "passed": source_item["kind"] == "telegram_channel"
                and len(source_item["messages"]) > 0,
                "detail": f"{len(source_item['messages'])} messages ready for Business Brain source learning",
                "data": {
                    "kind": source_item["kind"],
                    "label": source_item["label"],
                    "message_count": len(source_item["messages"]),
                },
            })

        passed = all(check["passed"] for check in checks)
        return {
            "passed": passed,
            "summary": (
                f"{sum(1 for check in checks if check['passed'])}/{len(checks)} live Telegram onboarding checks passed"
            ),
            "checks": checks,
            "source_item": _public_onboarding_source_item(source_item),
        }

    return asyncio.run(_check())


def _run_harness_parallel_harness() -> dict:
    """Run DB-backed proof suites concurrently without shared test DB races."""
    suites = [
        {
            "name": "delivery-chaos",
            "proof_paths": ["tests/test_delivery_chaos_harness.py"],
        },
        {
            "name": "media-chaos",
            "proof_paths": ["tests/test_media_chaos_harness.py"],
        },
        {
            "name": "embedding-chaos",
            "proof_paths": [
                "tests/test_embedding_batch.py",
                "tests/test_embedding_chaos_harness.py",
            ],
        },
    ]
    processes: list[tuple[dict, subprocess.Popen[str], str]] = []
    for sequence, suite in enumerate(suites, start=1):
        db_suffix = make_harness_db_suffix(suite["name"], sequence=sequence)
        process = subprocess.Popen(
            [
                _venv_python(BACKEND_DIR),
                "-m",
                "pytest",
                *suite["proof_paths"],
                "-q",
                "--no-cov",
            ],
            cwd=BACKEND_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=build_backend_pytest_env(
                db_suffix=db_suffix,
                drop_db_at_end=True,
            ),
        )
        processes.append((suite, process, db_suffix))

    results = []
    for suite, process, db_suffix in processes:
        stdout, stderr = process.communicate()
        output = stdout + stderr
        results.append({
            "name": suite["name"],
            "passed": process.returncode == 0,
            "summary": _parse_pytest_summary(output),
            "db_suffix": db_suffix,
            "proof_paths": suite["proof_paths"],
            "output": output,
        })

    passed = all(result["passed"] for result in results)
    return {
        "passed": passed,
        "summary": f"{sum(1 for result in results if result['passed'])}/{len(results)} parallel suites passed",
        "suites": results,
        "output": "\n\n".join(
            f"--- {result['name']} ({result['summary']}) ---\n{result['output']}"
            for result in results
        ),
    }


PHASE3_GATEWAY_BACKEND_PROOF_PATHS = [
    "tests/test_trigger_run_router.py",
    "tests/test_runtime_profile_compiler.py",
    "tests/test_runtime_profile_eval.py",
    "tests/test_agent_talking_contracts.py",
    "tests/test_runtime_signals.py::test_load_runtime_slo_signals_collects_telegram_trigger_start_p50",
    "tests/test_telegram_chat_memory_ingestion.py",
    "tests/test_chat_memory_pair_index_worker.py",
    "tests/test_chat_memory_extraction_worker.py",
    "tests/test_media_runtime.py::test_media_hydration_worker_executes_voice_and_photo_semantics",
    "tests/test_media_runtime.py::test_two_media_workers_do_not_process_same_due_job",
    "tests/test_media_runtime_boundary.py",
    "tests/test_replay_harness.py::test_replay_rebuilds_projection_idempotently_and_workspace_scoped",
    "tests/test_replay_harness.py::test_replay_rebuilds_delivery_runtime_projection_from_delivery_events",
]

PHASE4_KNOWLEDGE_BACKEND_PROOF_PATHS = [
    "tests/test_phase4_knowledge_mcp_agent_control.py",
    "tests/test_hermes_oqim_tools.py",
]

PHASE3_GATEWAY_REQUIRED_LIVE_GATES = [
    {
        "name": "live_operator_talk_bundle_delivery",
        "purpose": "restart API/sidecar, send a real customer message, and prove talk.presence, talk.bundle, and Telegram delivery",
    },
    {
        "name": "live_10_message_trigger_start_p50",
        "purpose": "runtime-signals reports at least 10 live Telegram trigger-start samples with p50 <= 1000ms",
    },
    {
        "name": "live_restart_long_idle",
        "purpose": "stored sessions receive live updates after restart/long idle before background catch-up completes",
    },
    {
        "name": "live_profile_trigger_modes",
        "purpose": "reply, personal/owner-only, broadcast, and scanner profile triggers are proven against live gateway events",
    },
    {
        "name": "live_multi_workspace_flood_wait_isolation",
        "purpose": "one workspace's real Telegram flood-wait/background sync does not block another workspace's live trigger path",
    },
]


def _run_phase3_gateway_local_proofs() -> dict:
    """Run the local Phase 3 gateway contract proofs."""
    backend_result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            *PHASE3_GATEWAY_BACKEND_PROOF_PATHS,
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    sidecar_result = subprocess.run(
        ["node", "--test"],
        cwd=GRAMJS_SIDECAR_DIR,
        capture_output=True,
        text=True,
    )
    backend_output = backend_result.stdout + backend_result.stderr
    sidecar_output = sidecar_result.stdout + sidecar_result.stderr
    checks = [
        {
            "name": "local_backend_phase3_gateway_contracts",
            "passed": backend_result.returncode == 0,
            "status": "pass" if backend_result.returncode == 0 else "fail",
            "detail": _parse_pytest_summary(backend_output),
            "proof_paths": PHASE3_GATEWAY_BACKEND_PROOF_PATHS,
            "output": backend_output,
        },
        {
            "name": "local_gramjs_gateway_contracts",
            "passed": sidecar_result.returncode == 0,
            "status": "pass" if sidecar_result.returncode == 0 else "fail",
            "detail": _parse_node_test_summary(sidecar_output),
            "proof_paths": ["gramjs-sidecar/node --test"],
            "output": sidecar_output,
        },
    ]
    return {
        "passed": all(check["passed"] for check in checks),
        "summary": f"{sum(1 for check in checks if check['passed'])}/{len(checks)} local proof groups passed",
        "checks": checks,
        "output": "\n--- backend ---\n"
        + backend_output
        + "\n--- sidecar ---\n"
        + sidecar_output,
    }


def _run_phase4_knowledge_local_proofs() -> dict:
    """Run the local Phase 4 Knowledge MCP and Agent Control proof gate."""
    backend_result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m",
            "pytest",
            *PHASE4_KNOWLEDGE_BACKEND_PROOF_PATHS,
            "-q",
            "--no-cov",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    output = backend_result.stdout + backend_result.stderr
    checks = [
        {
            "name": "phase4_knowledge_mcp_agent_control_contracts",
            "passed": backend_result.returncode == 0,
            "status": "pass" if backend_result.returncode == 0 else "fail",
            "detail": _parse_pytest_summary(output),
            "proof_paths": PHASE4_KNOWLEDGE_BACKEND_PROOF_PATHS,
            "output": output,
        }
    ]
    return {
        "passed": all(check["passed"] for check in checks),
        "summary": f"{sum(1 for check in checks if check['passed'])}/{len(checks)} proof groups passed",
        "checks": checks,
        "output": output,
    }


def _load_phase3_gateway_live_evidence(live_evidence_file: str | None) -> tuple[dict, str | None]:
    """Load optional manual live gate evidence keyed by gate name."""
    if not live_evidence_file:
        return {}, None

    evidence_path = Path(live_evidence_file).expanduser()
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"could not read live evidence file {evidence_path}: {exc}"

    if not isinstance(payload, dict):
        return {}, f"live evidence file {evidence_path} must contain a JSON object"
    gates = payload.get("gates", payload)
    if not isinstance(gates, dict):
        return {}, f"live evidence file {evidence_path} field 'gates' must be a JSON object"
    return gates, None


def _phase3_gateway_evidence_check(gate: dict, evidence: dict) -> dict:
    name = gate["name"]
    entry = evidence.get(name)
    if not isinstance(entry, dict):
        return {
            "name": name,
            "passed": False,
            "status": "missing_evidence",
            "detail": gate["purpose"],
        }
    passed = bool(entry.get("passed"))
    evidence = entry.get("evidence")
    recorded_at = entry.get("recorded_at")
    if passed and (not recorded_at or evidence in (None, "", [], {})):
        return {
            "name": name,
            "passed": False,
            "status": "missing_artifact",
            "detail": (
                "manual live evidence requires recorded_at plus a non-empty "
                "evidence artifact"
            ),
            "evidence": evidence,
            "recorded_at": recorded_at,
        }
    return {
        "name": name,
        "passed": passed,
        "status": "pass" if passed else "fail",
        "detail": str(entry.get("detail") or gate["purpose"]),
        "evidence": evidence,
        "recorded_at": recorded_at,
    }


def _phase3_gateway_operator_check_from_payload(payload: dict, *, period_days: int) -> dict:
    operator_run = payload.get("operator_run") if isinstance(payload, dict) else None
    workspace_id = payload.get("workspace_id") if isinstance(payload, dict) else None
    if not isinstance(operator_run, dict) or not operator_run:
        return {
            "name": "live_operator_talk_bundle_delivery",
            "passed": False,
            "status": "missing_live_operator_run",
            "detail": (
                "no recent live Telegram HermesRun was found with operator "
                "presence, talk bundle, and delivery evidence"
            ),
            "data": {
                "workspace_id": workspace_id,
                "period_days": period_days,
            },
        }

    presence_payload = operator_run.get("presence_payload")
    if not isinstance(presence_payload, dict):
        presence_payload = {}
    action_count = int(operator_run.get("talk_bundle_action_count") or 0)
    delivered_message_id = (
        operator_run.get("external_message_id")
        or operator_run.get("telegram_message_id")
        or operator_run.get("delivery_message_id")
    )
    requirements = [
        (
            bool(operator_run.get("has_trigger_telemetry")),
            "missing_live_telemetry",
            "live trigger telemetry missing",
        ),
        (
            operator_run.get("presence_state") == "ok"
            and presence_payload.get("online") is True
            and presence_payload.get("read") is True
            and presence_payload.get("typing") is True,
            "missing_presence",
            "talk.presence did not prove online/read/typing success",
        ),
        (
            operator_run.get("talk_bundle_state") == "queued" and action_count > 0,
            "missing_talk_bundle",
            "talk.bundle queued event missing or empty",
        ),
        (
            operator_run.get("reply_status") == "sent",
            "missing_sent_reply",
            "seller reply is not marked sent",
        ),
        (
            operator_run.get("delivery_state") == "confirmed"
            and delivered_message_id is not None,
            "missing_delivery_confirmation",
            "Telegram delivery confirmation is missing",
        ),
    ]
    failed = next((item for item in requirements if not item[0]), None)
    passed = failed is None
    status = "pass" if passed else str(failed[1])
    reason = "all live operator requirements satisfied" if passed else str(failed[2])
    detail = (
        f"{reason}; workspace={workspace_id}, period_days={period_days}, "
        f"run={operator_run.get('run_id')}, created_at={operator_run.get('run_created_at')}, "
        f"presence={operator_run.get('presence_state')}, "
        f"bundle_actions={action_count}, reply_status={operator_run.get('reply_status')}, "
        f"delivery={operator_run.get('delivery_state')}"
    )
    return {
        "name": "live_operator_talk_bundle_delivery",
        "passed": passed,
        "status": status,
        "detail": detail,
        "data": {
            "workspace_id": workspace_id,
            "period_days": period_days,
            "operator_run": operator_run,
        },
    }


def _run_phase3_gateway_operator_live_check(
    *,
    workspace_id: int | None,
    period_days: int,
) -> dict:
    """Find real operator evidence for presence, talk bundle, and delivery."""
    requested_workspace_id = "None" if workspace_id is None else str(int(workspace_id))
    script = f"""
import asyncio
import json

async def main():
    from sqlalchemy import text
    from app.db.session import async_session

    async with async_session() as session:
        selected_workspace_id = {requested_workspace_id}
        if selected_workspace_id is None:
            row = await session.execute(text(
                \"\"\"
                select w.id
                from workspaces w
                join telegram_sessions ts on ts.workspace_id = w.id
                where coalesce(w.telegram_connected, false) is true
                order by ts.updated_at desc nulls last, w.updated_at desc nulls last, w.id desc
                limit 1
                \"\"\"
            ))
            selected_workspace_id = row.scalar_one_or_none()
        if selected_workspace_id is None:
            print(json.dumps({{
                "phase3_gateway_error": "no_connected_telegram_workspace"
            }}))
            return

        result = await session.execute(
            text(
                \"\"\"
                with candidates as (
                    select
                        hr.run_id,
                        hr.created_at as run_created_at,
                        (hr.details ? 'trigger_telemetry') as has_trigger_telemetry,
                        presence.tool_state as presence_state,
                        presence.payload as presence_payload,
                        bundle.tool_state as talk_bundle_state,
                        coalesce((bundle.payload ->> 'action_count')::int, 0)
                            as talk_bundle_action_count,
                        hr.output_ref,
                        ar.id as reply_id,
                        ar.status as reply_status,
                        ar.actually_sent_at as reply_sent_at,
                        ar.message_id as reply_message_id,
                        coalesce(dr.state, m.delivery_state) as delivery_state,
                        dr.message_id as delivery_message_id,
                        coalesce(dr.external_message_id, m.external_message_id)
                            as external_message_id,
                        m.telegram_message_id
                    from hermes_runs hr
                    left join hermes_run_events presence
                        on presence.hermes_run_id = hr.id
                        and presence.tool_name = 'talk.presence'
                    left join hermes_run_events bundle
                        on bundle.hermes_run_id = hr.id
                        and bundle.tool_name = 'talk.bundle'
                    left join ai_replies ar
                        on hr.output_ref ~ '^seller_agent_reply:[0-9]+$'
                        and ar.id = split_part(hr.output_ref, ':', 2)::int
                    left join lateral (
                        select
                            dr.state,
                            dr.message_id,
                            dr.external_message_id
                        from delivery_runtime dr
                        where dr.ai_reply_id = ar.id
                        and dr.state = 'confirmed'
                        and coalesce(dr.external_message_id, '') <> ''
                        order by dr.confirmed_at asc nulls last, dr.id asc
                        limit 1
                    ) dr on true
                    left join messages m on m.id = ar.message_id
                    where hr.workspace_id = :workspace_id
                    and hr.trigger_type = 'telegram_message'
                    and hr.created_at >= now() - (:period_days * interval '1 day')
                )
                select *
                from candidates
                order by (
                    has_trigger_telemetry is true
                    and presence_state = 'ok'
                    and coalesce((presence_payload ->> 'online')::boolean, false)
                    and coalesce((presence_payload ->> 'read')::boolean, false)
                    and coalesce((presence_payload ->> 'typing')::boolean, false)
                    and talk_bundle_state = 'queued'
                    and talk_bundle_action_count > 0
                    and reply_status = 'sent'
                    and delivery_state = 'confirmed'
                    and coalesce(external_message_id, telegram_message_id::text, delivery_message_id::text, '') <> ''
                ) desc,
                run_created_at desc
                limit 1
                \"\"\"
            ),
            {{
                "workspace_id": int(selected_workspace_id),
                "period_days": int({int(period_days)}),
            }},
        )
        row = result.mappings().one_or_none()
        payload = {{"workspace_id": int(selected_workspace_id)}}
        if row is not None:
            payload["operator_run"] = dict(row)
        print(json.dumps(payload, default=str))

asyncio.run(main())
"""
    try:
        result = subprocess.run(
            [_venv_python(BACKEND_DIR), "-c", script],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": "live_operator_talk_bundle_delivery",
            "passed": False,
            "status": "timeout",
            "detail": "operator live evidence query timed out",
            "output": str(exc),
        }

    output = result.stdout + result.stderr
    if result.returncode != 0:
        return {
            "name": "live_operator_talk_bundle_delivery",
            "passed": False,
            "status": "unreachable",
            "detail": "operator live evidence could not be loaded from the local backend environment",
            "output": _tail_output(output),
        }

    payload: dict | None = None
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if payload is None:
        return {
            "name": "live_operator_talk_bundle_delivery",
            "passed": False,
            "status": "invalid_output",
            "detail": "operator live evidence query did not emit parseable JSON",
            "output": _tail_output(output),
        }
    if payload.get("phase3_gateway_error") == "no_connected_telegram_workspace":
        return {
            "name": "live_operator_talk_bundle_delivery",
            "passed": False,
            "status": "no_connected_workspace",
            "detail": (
                "no Telegram-connected workspace was found; pass --workspace-id "
                "to evaluate a specific workspace"
            ),
        }
    return _phase3_gateway_operator_check_from_payload(
        payload,
        period_days=period_days,
    )


def _parse_phase3_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _coerce_phase3_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _phase3_gateway_restart_long_idle_check_from_payload(payload: dict) -> dict:
    sidecar = payload.get("sidecar") if isinstance(payload, dict) else None
    workspace_id = payload.get("workspace_id") if isinstance(payload, dict) else None
    if not isinstance(sidecar, dict):
        sidecar = {}

    handlers_at_raw = sidecar.get("handlersRegisteredAt")
    catchup_scheduled_raw = sidecar.get("catchUpScheduledAt")
    catchup_completed_raw = sidecar.get("lastCatchUpAt")
    live_at_raw = sidecar.get("lastLiveInboundHotPathAt")
    handlers_at = _parse_phase3_timestamp(handlers_at_raw)
    catchup_scheduled_at = _parse_phase3_timestamp(catchup_scheduled_raw)
    catchup_completed_at = _parse_phase3_timestamp(catchup_completed_raw)
    live_at = _parse_phase3_timestamp(live_at_raw)
    live_latency_ms = _coerce_phase3_float(
        sidecar.get("lastLiveInboundHotPathLatencyMs")
    )
    live_source = str(sidecar.get("lastInboundHotPathSource") or "")
    live_before_or_during_catchup = (
        live_at is None
        or catchup_completed_at is None
        or live_at <= catchup_completed_at
    )
    live_fast_after_handlers = (
        live_at is not None
        and handlers_at is not None
        and handlers_at <= live_at
        and live_source == "live"
        and live_latency_ms is not None
        and live_latency_ms <= 1000
    )

    requirements = [
        (
            sidecar.get("state") == "connected",
            "sidecar_not_connected",
            "sidecar is not connected",
        ),
        (
            handlers_at is not None,
            "handlers_not_registered",
            "live update handlers have no registration timestamp",
        ),
        (
            catchup_scheduled_at is not None,
            "background_recovery_not_scheduled",
            "background catch-up scheduling timestamp is missing",
        ),
        (
            handlers_at is None
            or catchup_scheduled_at is None
            or handlers_at <= catchup_scheduled_at,
            "background_recovery_before_handlers",
            "background catch-up was scheduled before live handlers registered",
        ),
        (
            live_at is not None,
            "missing_live_update",
            "no true live inbound update has been observed after restart",
        ),
        (
            live_at is None or handlers_at is None or handlers_at <= live_at,
            "live_before_handlers",
            "live update timestamp predates handler registration",
        ),
        (
            live_before_or_during_catchup or live_fast_after_handlers,
            "live_after_catchup_completed",
            "live update arrived after background catch-up and was not a fast live-handler proof",
        ),
    ]
    failed = next((item for item in requirements if not item[0]), None)
    passed = failed is None
    status = "pass" if passed else str(failed[1])
    reason = "restart/long-idle live proof satisfied" if passed else str(failed[2])
    detail = (
        f"{reason}; workspace={workspace_id}, state={sidecar.get('state')}, "
        f"handlers_at={handlers_at_raw}, catchup_scheduled_at={catchup_scheduled_raw}, "
        f"live_at={live_at_raw}, catchup_completed_at={catchup_completed_raw}, "
        f"live_latency_ms={sidecar.get('lastLiveInboundHotPathLatencyMs')}"
    )
    return {
        "name": "live_restart_long_idle",
        "passed": passed,
        "status": status,
        "detail": detail,
        "data": payload,
    }


def _run_phase3_gateway_restart_long_idle_live_check(
    *,
    workspace_id: int | None,
) -> dict:
    """Read sidecar status and require true live inbound after handler registration."""
    requested_workspace_id = "None" if workspace_id is None else str(int(workspace_id))
    script = f"""
import asyncio
import json
import urllib.parse
import urllib.request

async def main():
    from sqlalchemy import text
    from app.core.config import get_settings
    from app.db.session import async_session

    settings = get_settings()
    async with async_session() as session:
        selected_workspace_id = {requested_workspace_id}
        if selected_workspace_id is None:
            row = await session.execute(text(
                \"\"\"
                select w.id
                from workspaces w
                join telegram_sessions ts on ts.workspace_id = w.id
                where coalesce(w.telegram_connected, false) is true
                order by ts.updated_at desc nulls last, w.updated_at desc nulls last, w.id desc
                limit 1
                \"\"\"
            ))
            selected_workspace_id = row.scalar_one_or_none()
        if selected_workspace_id is None:
            print(json.dumps({{
                "phase3_gateway_error": "no_connected_telegram_workspace"
            }}))
            return

    sidecar_url = settings.sidecar_url.rstrip("/") + "/status?" + urllib.parse.urlencode({{
        "workspaceId": int(selected_workspace_id),
    }})
    headers = {{}}
    if settings.sidecar_api_key:
        headers["X-Sidecar-Key"] = settings.sidecar_api_key
    try:
        request = urllib.request.Request(sidecar_url, headers=headers)
        with urllib.request.urlopen(request, timeout=2) as response:
            raw_sidecar = json.loads(response.read().decode("utf-8"))
        if isinstance(raw_sidecar, dict):
            sidecar = {{
                "state": raw_sidecar.get("state"),
                "lastError": raw_sidecar.get("lastError"),
                "lastCatchUpAt": raw_sidecar.get("lastCatchUpAt"),
                "lastCatchUpCount": raw_sidecar.get("lastCatchUpCount"),
                "lastInboundHotPathAt": raw_sidecar.get("lastInboundHotPathAt"),
                "lastInboundHotPathLatencyMs": raw_sidecar.get(
                    "lastInboundHotPathLatencyMs"
                ),
                "lastInboundHotPathSource": raw_sidecar.get("lastInboundHotPathSource"),
                "lastLiveInboundHotPathAt": raw_sidecar.get("lastLiveInboundHotPathAt"),
                "lastLiveInboundHotPathLatencyMs": raw_sidecar.get(
                    "lastLiveInboundHotPathLatencyMs"
                ),
                "handlersRegisteredAt": raw_sidecar.get("handlersRegisteredAt"),
                "catchUpScheduledAt": raw_sidecar.get("catchUpScheduledAt"),
                "catchUpStartedAt": raw_sidecar.get("catchUpStartedAt"),
            }}
        else:
            sidecar = {{}}
    except Exception as exc:
        sidecar = {{"error": str(exc)}}

    print(json.dumps({{
        "workspace_id": int(selected_workspace_id),
        "sidecar": sidecar,
    }}, default=str))

asyncio.run(main())
"""
    try:
        result = subprocess.run(
            [_venv_python(BACKEND_DIR), "-c", script],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": "live_restart_long_idle",
            "passed": False,
            "status": "timeout",
            "detail": "restart/long-idle sidecar status query timed out",
            "output": str(exc),
        }

    output = result.stdout + result.stderr
    if result.returncode != 0:
        return {
            "name": "live_restart_long_idle",
            "passed": False,
            "status": "unreachable",
            "detail": "restart/long-idle sidecar status could not be loaded",
            "output": _tail_output(output),
        }

    payload: dict | None = None
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if payload is None:
        return {
            "name": "live_restart_long_idle",
            "passed": False,
            "status": "invalid_output",
            "detail": "restart/long-idle sidecar status did not emit parseable JSON",
            "output": _tail_output(output),
        }
    if payload.get("phase3_gateway_error") == "no_connected_telegram_workspace":
        return {
            "name": "live_restart_long_idle",
            "passed": False,
            "status": "no_connected_workspace",
            "detail": (
                "no Telegram-connected workspace was found; pass --workspace-id "
                "to evaluate a specific workspace"
            ),
        }
    return _phase3_gateway_restart_long_idle_check_from_payload(payload)


def _profile_trigger_key(row: dict) -> str | None:
    run_mode = str(row.get("run_mode") or "")
    agent_kind = str(row.get("agent_kind") or "")
    if run_mode == "reply":
        return "reply"
    if run_mode == "personal" or agent_kind == "personal":
        return "personal"
    if run_mode == "broadcast" or agent_kind == "broadcast":
        return "broadcast"
    if run_mode == "scan" or agent_kind == "scanner":
        return "scanner"
    return None


def _phase3_gateway_profile_trigger_check_from_payload(
    payload: dict,
    *,
    period_days: int,
) -> dict:
    workspace_id = payload.get("workspace_id") if isinstance(payload, dict) else None
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(profiles, dict):
        profiles = {}
    required_profiles = ["reply", "personal", "broadcast", "scanner"]
    missing = [
        profile
        for profile in required_profiles
        if not isinstance(profiles.get(profile), dict)
        or not profiles[profile].get("run_id")
    ]
    if missing:
        return {
            "name": "live_profile_trigger_modes",
            "passed": False,
            "status": "missing_profile_modes",
            "detail": (
                f"missing live profile run evidence for {','.join(missing)}; "
                f"workspace={workspace_id}, period_days={period_days}"
            ),
            "data": {
                "workspace_id": workspace_id,
                "period_days": period_days,
                "profiles": profiles,
            },
        }

    reply = profiles["reply"]
    if not reply.get("has_trigger_telemetry"):
        return {
            "name": "live_profile_trigger_modes",
            "passed": False,
            "status": "missing_reply_live_telemetry",
            "detail": (
                "reply profile run exists but lacks live Telegram trigger telemetry; "
                f"workspace={workspace_id}, period_days={period_days}, "
                f"run={reply.get('run_id')}"
            ),
            "data": {
                "workspace_id": workspace_id,
                "period_days": period_days,
                "profiles": profiles,
            },
        }
    if reply.get("trigger_type") != "telegram_message":
        return {
            "name": "live_profile_trigger_modes",
            "passed": False,
            "status": "reply_not_telegram",
            "detail": (
                "reply profile proof must come from a live Telegram message trigger; "
                f"workspace={workspace_id}, period_days={period_days}, "
                f"trigger_type={reply.get('trigger_type')}"
            ),
            "data": {
                "workspace_id": workspace_id,
                "period_days": period_days,
                "profiles": profiles,
            },
        }

    return {
        "name": "live_profile_trigger_modes",
        "passed": True,
        "status": "pass",
        "detail": (
            "all Phase 3 profile trigger modes have live DB evidence; "
            f"workspace={workspace_id}, period_days={period_days}, "
            "profiles=reply,personal,broadcast,scanner"
        ),
        "data": {
            "workspace_id": workspace_id,
            "period_days": period_days,
            "profiles": profiles,
        },
    }


def _run_phase3_gateway_profile_trigger_live_check(
    *,
    workspace_id: int | None,
    period_days: int,
) -> dict:
    """Find live DB evidence for reply, personal, broadcast, and scanner profiles."""
    requested_workspace_id = "None" if workspace_id is None else str(int(workspace_id))
    script = f"""
import asyncio
import json

async def main():
    from sqlalchemy import text
    from app.db.session import async_session

    async with async_session() as session:
        selected_workspace_id = {requested_workspace_id}
        if selected_workspace_id is None:
            row = await session.execute(text(
                \"\"\"
                select w.id
                from workspaces w
                join telegram_sessions ts on ts.workspace_id = w.id
                where coalesce(w.telegram_connected, false) is true
                order by ts.updated_at desc nulls last, w.updated_at desc nulls last, w.id desc
                limit 1
                \"\"\"
            ))
            selected_workspace_id = row.scalar_one_or_none()
        if selected_workspace_id is None:
            print(json.dumps({{
                "phase3_gateway_error": "no_connected_telegram_workspace"
            }}))
            return

        result = await session.execute(
            text(
                \"\"\"
                select
                    run_id,
                    run_mode,
                    agent_kind,
                    lane,
                    trigger_type,
                    event_id,
                    state,
                    created_at,
                    details ? 'trigger_telemetry' as has_trigger_telemetry
                from hermes_runs
                where workspace_id = :workspace_id
                and created_at >= now() - (:period_days * interval '1 day')
                and (
                    run_mode in ('reply', 'personal', 'broadcast', 'scan')
                    or agent_kind in ('personal', 'broadcast', 'scanner')
                )
                order by created_at desc, id desc
                limit 200
                \"\"\"
            ),
            {{
                "workspace_id": int(selected_workspace_id),
                "period_days": int({int(period_days)}),
            }},
        )

        profiles = {{}}
        for row in result.mappings().all():
            item = dict(row)
            run_mode = str(item.get("run_mode") or "")
            agent_kind = str(item.get("agent_kind") or "")
            if run_mode == "reply":
                profile = "reply"
            elif run_mode == "personal" or agent_kind == "personal":
                profile = "personal"
            elif run_mode == "broadcast" or agent_kind == "broadcast":
                profile = "broadcast"
            elif run_mode == "scan" or agent_kind == "scanner":
                profile = "scanner"
            else:
                continue
            current = profiles.get(profile)
            is_better = (
                profile == "reply"
                and item.get("has_trigger_telemetry") is True
                and item.get("trigger_type") == "telegram_message"
                and not (
                    current
                    and current.get("has_trigger_telemetry") is True
                    and current.get("trigger_type") == "telegram_message"
                )
            )
            if current is None or is_better:
                profiles[profile] = item

        print(json.dumps({{
            "workspace_id": int(selected_workspace_id),
            "profiles": profiles,
        }}, default=str))

asyncio.run(main())
"""
    try:
        result = subprocess.run(
            [_venv_python(BACKEND_DIR), "-c", script],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": "live_profile_trigger_modes",
            "passed": False,
            "status": "timeout",
            "detail": "profile trigger evidence query timed out",
            "output": str(exc),
        }

    output = result.stdout + result.stderr
    if result.returncode != 0:
        return {
            "name": "live_profile_trigger_modes",
            "passed": False,
            "status": "unreachable",
            "detail": "profile trigger evidence could not be loaded from the local backend environment",
            "output": _tail_output(output),
        }

    payload: dict | None = None
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if payload is None:
        return {
            "name": "live_profile_trigger_modes",
            "passed": False,
            "status": "invalid_output",
            "detail": "profile trigger evidence query did not emit parseable JSON",
            "output": _tail_output(output),
        }
    if payload.get("phase3_gateway_error") == "no_connected_telegram_workspace":
        return {
            "name": "live_profile_trigger_modes",
            "passed": False,
            "status": "no_connected_workspace",
            "detail": (
                "no Telegram-connected workspace was found; pass --workspace-id "
                "to evaluate a specific workspace"
            ),
        }
    return _phase3_gateway_profile_trigger_check_from_payload(
        payload,
        period_days=period_days,
    )


def _phase3_gateway_multi_workspace_check_from_payload(payload: dict) -> dict:
    def number(value: object) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0

    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    queried_at_raw = payload.get("queriedAt") if isinstance(payload, dict) else None
    queried_at = _parse_phase3_timestamp(
        queried_at_raw
    )
    if not isinstance(sessions, list):
        sessions = []
    data = {"queriedAt": queried_at_raw, "sessions": sessions}
    local_flood_wait_proof = (
        payload.get("localFloodWaitIsolationProof")
        if isinstance(payload, dict)
        else None
    )
    connected = [
        session
        for session in sessions
        if isinstance(session, dict) and session.get("state") == "connected"
    ]
    if len(connected) < 2:
        return {
            "name": "live_multi_workspace_flood_wait_isolation",
            "passed": False,
            "status": "missing_two_connected_workspaces",
            "detail": (
                "live multi-workspace proof requires at least two connected "
                f"workspaces; connected={len(connected)}"
            ),
            "data": data,
        }

    flooded = next(
        (
            (session, flood_wait)
            for session in connected
            for flood_wait in session.get("telegramFloodWaits") or []
            if isinstance(flood_wait, dict)
            and number(flood_wait.get("pausedForMs")) > 0
        ),
        None,
    )
    if flooded is None:
        live_session = next(
            (
                session
                for session in connected
                if _parse_phase3_timestamp(session.get("lastLiveInboundHotPathAt"))
            ),
            None,
        )
        if (
            isinstance(local_flood_wait_proof, dict)
            and local_flood_wait_proof.get("passed") is True
            and live_session is not None
        ):
            data["localFloodWaitIsolationProof"] = local_flood_wait_proof
            return {
                "name": "live_multi_workspace_flood_wait_isolation",
                "passed": True,
                "status": "pass",
                "detail": (
                    "controlled flood-wait isolation proof passed with live "
                    "multi-workspace sessions; "
                    f"connected={len(connected)}, "
                    f"live_workspace={live_session.get('workspaceId')}"
                ),
                "data": data,
            }
        return {
            "name": "live_multi_workspace_flood_wait_isolation",
            "passed": False,
            "status": "missing_live_flood_wait",
            "detail": (
                "no connected workspace currently exposes a real Telegram flood-wait "
                "and controlled flood-wait isolation proof is missing"
            ),
            "data": data,
        }

    flooded_session, flood_wait = flooded
    retry_after = number(flood_wait.get("retryAfter"))
    paused_for_ms = number(flood_wait.get("pausedForMs"))
    if queried_at is None or retry_after <= 0 or paused_for_ms <= 0:
        return {
            "name": "live_multi_workspace_flood_wait_isolation",
            "passed": False,
            "status": "missing_flood_wait_timing",
            "detail": "active flood-wait evidence is missing query/retry timing",
            "data": data,
        }

    flood_started_at = queried_at - timedelta(
        milliseconds=max(0, retry_after * 1000 - paused_for_ms)
    )
    flooded_workspace_id = int(flooded_session.get("workspaceId") or 0)
    live = next(
        (
            session
            for session in connected
            if int(session.get("workspaceId") or 0) != flooded_workspace_id
            and (
                live_at := _parse_phase3_timestamp(
                    session.get("lastLiveInboundHotPathAt")
                )
            )
            and live_at >= flood_started_at
        ),
        None,
    )
    if live is None:
        return {
            "name": "live_multi_workspace_flood_wait_isolation",
            "passed": False,
            "status": "missing_isolated_live_workspace",
            "detail": (
                "no other connected workspace has true live inbound while a "
                "flood-wait is active; "
                f"flooded_workspace={flooded_workspace_id}, "
                f"flood_started_at={flood_started_at.isoformat()}"
            ),
            "data": data,
        }

    live_workspace_id = int(live.get("workspaceId") or 0)
    return {
        "name": "live_multi_workspace_flood_wait_isolation",
        "passed": True,
        "status": "pass",
        "detail": (
            "real flood-wait isolation evidence found; "
            f"flooded_workspace={flooded_workspace_id}, "
            f"live_workspace={live_workspace_id}, "
            f"flood_started_at={flood_started_at.isoformat()}, "
            f"live_at={live.get('lastLiveInboundHotPathAt')}"
        ),
        "data": data,
    }


def _run_phase3_gateway_multi_workspace_live_check() -> dict:
    """Read sidecar sessions and require real flood-wait/live isolation."""
    script = """
import asyncio
import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

async def main():
    from app.core.config import get_settings

    queried_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    settings = get_settings()
    sidecar_url = settings.sidecar_url.rstrip("/") + "/sessions"
    headers = {}
    if settings.sidecar_api_key:
        headers["X-Sidecar-Key"] = settings.sidecar_api_key
    try:
        request = urllib.request.Request(sidecar_url, headers=headers)
        with urllib.request.urlopen(request, timeout=2) as response:
            raw_sessions = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(json.dumps({"phase3_gateway_error": str(exc)}))
        return

    sessions = []
    if isinstance(raw_sessions, list):
        for raw in raw_sessions:
            if not isinstance(raw, dict):
                continue
            sessions.append({
                "workspaceId": raw.get("workspaceId"),
                "state": raw.get("state"),
                "lastError": raw.get("lastError"),
                "lastInboundHotPathSource": raw.get("lastInboundHotPathSource"),
                "lastLiveInboundHotPathAt": raw.get("lastLiveInboundHotPathAt"),
                "lastLiveInboundHotPathLatencyMs": raw.get(
                    "lastLiveInboundHotPathLatencyMs"
                ),
                "telegramFloodWaits": raw.get("telegramFloodWaits") or [],
            })
    sidecar_dir = Path.cwd().parent / "gramjs-sidecar"
    local_proof = {"passed": False, "summary": "not_run"}
    try:
        proof = subprocess.run(
            ["node", "--test", "telegram-flood-wait-isolation.test.js"],
            cwd=sidecar_dir,
            capture_output=True,
            text=True,
            timeout=15,
        )
        local_proof = {
            "passed": proof.returncode == 0,
            "summary": "pass" if proof.returncode == 0 else "fail",
        }
    except Exception as exc:
        local_proof = {"passed": False, "summary": str(exc)}
    print(json.dumps({
        "queriedAt": queried_at,
        "sessions": sessions,
        "localFloodWaitIsolationProof": local_proof,
    }, default=str))

asyncio.run(main())
"""
    try:
        result = subprocess.run(
            [_venv_python(BACKEND_DIR), "-c", script],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": "live_multi_workspace_flood_wait_isolation",
            "passed": False,
            "status": "timeout",
            "detail": "multi-workspace sidecar sessions query timed out",
            "output": str(exc),
        }

    output = result.stdout + result.stderr
    if result.returncode != 0:
        return {
            "name": "live_multi_workspace_flood_wait_isolation",
            "passed": False,
            "status": "unreachable",
            "detail": "multi-workspace sidecar sessions could not be loaded",
            "output": _tail_output(output),
        }

    payload: dict | None = None
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if payload is None:
        return {
            "name": "live_multi_workspace_flood_wait_isolation",
            "passed": False,
            "status": "invalid_output",
            "detail": "multi-workspace sidecar sessions did not emit parseable JSON",
            "output": _tail_output(output),
        }
    if payload.get("phase3_gateway_error"):
        return {
            "name": "live_multi_workspace_flood_wait_isolation",
            "passed": False,
            "status": "unreachable",
            "detail": str(payload.get("phase3_gateway_error")),
        }
    return _phase3_gateway_multi_workspace_check_from_payload(payload)


def _phase3_gateway_trigger_diagnostics_detail(diagnostics: dict | None) -> str:
    if not isinstance(diagnostics, dict) or not diagnostics:
        return ""

    parts: list[str] = []
    if "telegram_runs" in diagnostics:
        parts.append(f"telegram_runs={diagnostics.get('telegram_runs')}")
    if "telegram_runs_with_trigger_telemetry" in diagnostics:
        parts.append(
            "trigger_telemetry_runs="
            f"{diagnostics.get('telegram_runs_with_trigger_telemetry')}"
        )
    if diagnostics.get("latest_telegram_run_created_at") is not None:
        parts.append(
            "latest_telegram_run_at="
            f"{diagnostics.get('latest_telegram_run_created_at')}"
        )
    if diagnostics.get("latest_trigger_telemetry_run_created_at") is not None:
        parts.append(
            "latest_trigger_telemetry_run_at="
            f"{diagnostics.get('latest_trigger_telemetry_run_created_at')}"
        )

    sidecar = diagnostics.get("sidecar")
    if isinstance(sidecar, dict):
        if sidecar.get("state") is not None:
            parts.append(f"sidecar_state={sidecar.get('state')}")
        if sidecar.get("lastInboundHotPathSource") is not None:
            parts.append(
                "sidecar_hot_path_source="
                f"{sidecar.get('lastInboundHotPathSource')}"
            )
        parts.append(f"sidecar_live_at={sidecar.get('lastLiveInboundHotPathAt')}")

    return ", ".join(parts)


def _phase3_gateway_trigger_start_check_from_payload(
    payload: dict,
    *,
    workspace_id: int | None,
    period_days: int,
) -> dict:
    slo = payload.get("slo") if isinstance(payload, dict) else {}
    if not isinstance(slo, dict):
        slo = {}
    selected_workspace_id = int(payload.get("workspace_id") or workspace_id or 0)
    status = str(slo.get("telegram_trigger_start_under_1s_status") or "missing")
    p50_ms = slo.get("telegram_trigger_start_p50_ms")
    sample_count = int(slo.get("telegram_trigger_start_sample_count") or 0)
    required_count = int(slo.get("telegram_trigger_start_required_sample_count") or 10)
    diagnostics = payload.get("phase3_gateway_diagnostics")
    diagnostics_detail = _phase3_gateway_trigger_diagnostics_detail(
        diagnostics if isinstance(diagnostics, dict) else None
    )
    passed = (
        status == "ok"
        and sample_count >= required_count
        and p50_ms is not None
        and int(p50_ms) <= 1000
    )
    detail = (
        f"status={status}, p50_ms={p50_ms}, samples={sample_count}/{required_count}, "
        f"workspace={selected_workspace_id}, period_days={period_days}"
    )
    if diagnostics_detail:
        detail += f", diagnostics: {diagnostics_detail}"

    data = {
        "workspace_id": selected_workspace_id,
        "period_days": period_days,
        "telegram_trigger_start_under_1s_status": status,
        "telegram_trigger_start_p50_ms": p50_ms,
        "telegram_trigger_start_sample_count": sample_count,
        "telegram_trigger_start_required_sample_count": required_count,
    }
    if isinstance(diagnostics, dict):
        data["diagnostics"] = diagnostics

    return {
        "name": "live_10_message_trigger_start_p50",
        "passed": passed,
        "status": "pass" if passed else status,
        "detail": detail,
        "data": data,
    }


def _run_phase3_gateway_trigger_start_live_check(
    *,
    workspace_id: int | None,
    period_days: int,
) -> dict:
    """Resolve the Phase 3 live workspace from connected Telegram state."""
    requested_workspace_id = "None" if workspace_id is None else str(int(workspace_id))
    script = f"""
import asyncio
import json

async def main():
    import redis.asyncio as aioredis
    import urllib.parse
    import urllib.request
    from sqlalchemy import text
    from app.core.config import get_settings
    from app.db.session import async_session
    from app.services.runtime_signals import load_runtime_signals

    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with async_session() as session:
            selected_workspace_id = {requested_workspace_id}
            if selected_workspace_id is None:
                row = await session.execute(text(
                    \"\"\"
                    select w.id
                    from workspaces w
                    join telegram_sessions ts on ts.workspace_id = w.id
                    where coalesce(w.telegram_connected, false) is true
                    order by ts.updated_at desc nulls last, w.updated_at desc nulls last, w.id desc
                    limit 1
                    \"\"\"
                ))
                selected_workspace_id = row.scalar_one_or_none()
            if selected_workspace_id is None:
                print(json.dumps({{
                    "phase3_gateway_error": "no_connected_telegram_workspace"
                }}))
                return
            diagnostics_row = await session.execute(
                text(
                    \"\"\"
                    select
                        count(*)::int as telegram_runs,
                        count(*) filter (
                            where details ? 'trigger_telemetry'
                        )::int as telegram_runs_with_trigger_telemetry,
                        max(created_at) as latest_telegram_run_created_at,
                        max(created_at) filter (
                            where details ? 'trigger_telemetry'
                        ) as latest_trigger_telemetry_run_created_at
                    from hermes_runs
                    where workspace_id = :workspace_id
                    and trigger_type = 'telegram_message'
                    and created_at >= now() - (:period_days * interval '1 day')
                    \"\"\"
                ),
                {{
                    "workspace_id": int(selected_workspace_id),
                    "period_days": int({int(period_days)}),
                }},
            )
            diagnostics = dict(diagnostics_row.mappings().one())
            signals = await load_runtime_signals(
                session,
                redis,
                workspace_id=int(selected_workspace_id),
                period_days={int(period_days)},
            )
    finally:
        await redis.aclose()

    sidecar_status = {{}}
    try:
        sidecar_url = settings.sidecar_url.rstrip("/") + "/status?" + urllib.parse.urlencode({{
            "workspaceId": int(selected_workspace_id),
        }})
        headers = {{}}
        if settings.sidecar_api_key:
            headers["X-Sidecar-Key"] = settings.sidecar_api_key
        request = urllib.request.Request(sidecar_url, headers=headers)
        with urllib.request.urlopen(request, timeout=2) as response:
            raw_status = json.loads(response.read().decode("utf-8"))
        if isinstance(raw_status, dict):
            sidecar_status = {{
                "state": raw_status.get("state"),
                "lastInboundHotPathAt": raw_status.get("lastInboundHotPathAt"),
                "lastInboundHotPathLatencyMs": raw_status.get("lastInboundHotPathLatencyMs"),
                "lastInboundHotPathSource": raw_status.get("lastInboundHotPathSource"),
                "lastLiveInboundHotPathAt": raw_status.get("lastLiveInboundHotPathAt"),
                "lastLiveInboundHotPathLatencyMs": raw_status.get("lastLiveInboundHotPathLatencyMs"),
            }}
    except Exception as exc:
        sidecar_status = {{"error": str(exc)}}

    payload = signals.to_dict()
    payload["workspace_id"] = int(selected_workspace_id)
    payload["phase3_gateway_diagnostics"] = {{
        "telegram_runs": diagnostics.get("telegram_runs"),
        "telegram_runs_with_trigger_telemetry": diagnostics.get(
            "telegram_runs_with_trigger_telemetry"
        ),
        "latest_telegram_run_created_at": diagnostics.get(
            "latest_telegram_run_created_at"
        ),
        "latest_trigger_telemetry_run_created_at": diagnostics.get(
            "latest_trigger_telemetry_run_created_at"
        ),
        "sidecar": sidecar_status,
    }}
    print(json.dumps(payload, default=str))

asyncio.run(main())
"""
    try:
        result = subprocess.run(
            [_venv_python(BACKEND_DIR), "-c", script],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": "live_10_message_trigger_start_p50",
            "passed": False,
            "status": "timeout",
            "detail": "runtime-signals timed out before returning live p50 evidence",
            "output": str(exc),
        }

    output = result.stdout + result.stderr
    if result.returncode != 0:
        return {
            "name": "live_10_message_trigger_start_p50",
            "passed": False,
            "status": "unreachable",
            "detail": "runtime-signals could not be loaded from the local backend environment",
            "output": _tail_output(output),
        }

    payload: dict | None = None
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if payload is None:
        return {
            "name": "live_10_message_trigger_start_p50",
            "passed": False,
            "status": "invalid_output",
            "detail": "runtime-signals did not emit parseable JSON",
            "output": _tail_output(output),
        }
    if payload.get("phase3_gateway_error") == "no_connected_telegram_workspace":
        return {
            "name": "live_10_message_trigger_start_p50",
            "passed": False,
            "status": "no_connected_workspace",
            "detail": (
                "no Telegram-connected workspace was found; pass --workspace-id "
                "to evaluate a specific workspace"
            ),
        }

    return _phase3_gateway_trigger_start_check_from_payload(
        payload,
        workspace_id=workspace_id,
        period_days=period_days,
    )


def _phase3_gateway_live_gate_checks(
    *,
    local_only: bool,
    workspace_id: int | None,
    period_days: int,
    live_evidence_file: str | None,
) -> list[dict]:
    if local_only:
        return [
            {
                "name": gate["name"],
                "passed": None,
                "status": "not_evaluated",
                "detail": gate["purpose"],
            }
            for gate in PHASE3_GATEWAY_REQUIRED_LIVE_GATES
        ]

    evidence, evidence_error = _load_phase3_gateway_live_evidence(live_evidence_file)
    checks: list[dict] = []
    if evidence_error:
        checks.append({
            "name": "live_evidence_file",
            "passed": False,
            "status": "fail",
            "detail": evidence_error,
        })

    for gate in PHASE3_GATEWAY_REQUIRED_LIVE_GATES:
        if gate["name"] == "live_operator_talk_bundle_delivery" and gate["name"] not in evidence:
            checks.append(
                _run_phase3_gateway_operator_live_check(
                    workspace_id=workspace_id,
                    period_days=period_days,
                )
            )
        elif gate["name"] == "live_10_message_trigger_start_p50" and gate["name"] not in evidence:
            checks.append(
                _run_phase3_gateway_trigger_start_live_check(
                    workspace_id=workspace_id,
                    period_days=period_days,
                )
            )
        elif gate["name"] == "live_restart_long_idle" and gate["name"] not in evidence:
            checks.append(
                _run_phase3_gateway_restart_long_idle_live_check(
                    workspace_id=workspace_id,
                )
            )
        elif gate["name"] == "live_profile_trigger_modes" and gate["name"] not in evidence:
            checks.append(
                _run_phase3_gateway_profile_trigger_live_check(
                    workspace_id=workspace_id,
                    period_days=period_days,
                )
            )
        elif (
            gate["name"] == "live_multi_workspace_flood_wait_isolation"
            and gate["name"] not in evidence
        ):
            checks.append(_run_phase3_gateway_multi_workspace_live_check())
        else:
            checks.append(_phase3_gateway_evidence_check(gate, evidence))
    return checks


def _phase3_gateway_live_next_actions(live_checks: list[dict]) -> list[str]:
    actions: list[str] = []
    statuses = {
        str(check.get("name")): str(check.get("status") or "")
        for check in live_checks
        if check.get("passed") is not True
    }

    if (
        statuses.get("live_10_message_trigger_start_p50") in {"unmeasured", "missing"}
        or statuses.get("live_restart_long_idle") == "missing_live_update"
        or statuses.get("live_profile_trigger_modes") == "missing_reply_live_telemetry"
        or statuses.get("live_operator_talk_bundle_delivery")
        in {"missing_live_operator_run", "missing_live_telemetry"}
    ):
        actions.append(
            "Send 10 real inbound Telegram messages from another account to the "
            "connected workspace after restart, then rerun this gate."
        )
    if statuses.get("live_operator_talk_bundle_delivery") in {
        "missing_sent_reply",
        "missing_delivery_confirmation",
    }:
        actions.append(
            "Approve or allow one generated seller reply and wait for Telegram "
            "delivery confirmation."
        )
    if statuses.get("live_profile_trigger_modes") == "missing_profile_modes":
        actions.append(
            "Trigger personal, broadcast, and scanner profile paths in the "
            "live workspace so HermesRun rows exist for each non-reply mode."
        )
    if statuses.get("live_multi_workspace_flood_wait_isolation") == "missing_two_connected_workspaces":
        actions.append(
            "Connect a second Telegram workspace before the multi-workspace "
            "flood-wait isolation run; keep workspace 1 offline unless explicitly enabled."
        )
    if statuses.get("live_multi_workspace_flood_wait_isolation") in {
        "missing_live_flood_wait",
        "missing_flood_wait_timing",
        "missing_isolated_live_workspace",
    }:
        actions.append(
            "Capture an active real Telegram flood-wait on one connected workspace "
            "while another connected workspace receives a true live inbound message."
        )
    return actions


def _phase3_gateway_live_check_statuses(live_checks: list[dict]) -> dict[str, str]:
    return {
        str(check.get("name")): str(check.get("status") or "")
        for check in live_checks
        if check.get("passed") is not True
    }


def _phase3_gateway_workspace_from_live_checks(live_checks: list[dict]) -> int | None:
    for check in live_checks:
        data = check.get("data")
        if not isinstance(data, dict):
            continue
        workspace_id = data.get("workspace_id")
        if isinstance(workspace_id, int):
            return workspace_id
        if isinstance(workspace_id, str) and workspace_id.isdigit():
            return int(workspace_id)
    return None


def _phase3_gateway_connected_workspace_ids(live_checks: list[dict]) -> list[int]:
    for check in live_checks:
        if check.get("name") != "live_multi_workspace_flood_wait_isolation":
            continue
        data = check.get("data")
        if not isinstance(data, dict):
            return []
        sessions = data.get("sessions")
        if not isinstance(sessions, list):
            return []
        workspace_ids: list[int] = []
        for session in sessions:
            if not isinstance(session, dict) or session.get("state") != "connected":
                continue
            workspace_id = session.get("workspaceId")
            if isinstance(workspace_id, int):
                workspace_ids.append(workspace_id)
            elif isinstance(workspace_id, str) and workspace_id.isdigit():
                workspace_ids.append(int(workspace_id))
        return sorted(set(workspace_ids))
    return []


def _phase3_gateway_live_capture_plan(
    live_checks: list[dict],
    *,
    workspace_id: int | None = None,
    approve_live_marker: str | None = None,
) -> dict:
    statuses = _phase3_gateway_live_check_statuses(live_checks)
    selected_workspace_id = workspace_id or _phase3_gateway_workspace_from_live_checks(live_checks)
    marker = (approve_live_marker or "").strip() or "phase3-live-proof"
    needs_live_inbound = (
        statuses.get("live_10_message_trigger_start_p50") in {"unmeasured", "missing"}
        or statuses.get("live_restart_long_idle") == "missing_live_update"
        or statuses.get("live_profile_trigger_modes") == "missing_reply_live_telemetry"
        or statuses.get("live_operator_talk_bundle_delivery")
        in {"missing_live_operator_run", "missing_live_telemetry"}
    )
    needs_reply_delivery = statuses.get("live_operator_talk_bundle_delivery") in {
        "missing_sent_reply",
        "missing_delivery_confirmation",
        "missing_live_telemetry",
        "missing_live_operator_run",
    }
    needs_second_workspace = statuses.get("live_multi_workspace_flood_wait_isolation") == (
        "missing_two_connected_workspaces"
    )
    needs_live_flood_wait = statuses.get("live_multi_workspace_flood_wait_isolation") in {
        "missing_live_flood_wait",
        "missing_flood_wait_timing",
        "missing_isolated_live_workspace",
    }

    command_parts = [
        "backend/.venv/bin/python",
        "-m",
        "cli.__main__",
        "test",
        "phase3-gateway",
    ]
    if selected_workspace_id is not None:
        command_parts.extend(["--workspace-id", str(selected_workspace_id)])
    if needs_reply_delivery:
        command_parts.extend([
            "--approve-live-reply",
            "--approve-live-marker",
            marker,
        ])
    command_parts.extend(["--live-wait-seconds", "300", "--live-poll-seconds", "5", "--json"])
    connected_workspace_ids = _phase3_gateway_connected_workspace_ids(live_checks)

    return {
        "workspace_id": selected_workspace_id,
        "required_live_messages": 10 if needs_live_inbound else 0,
        "send_from": "another Telegram account",
        "message_marker": marker if needs_reply_delivery else None,
        "approval_helper_enabled": needs_reply_delivery,
        "wait_command": " ".join(command_parts),
        "connected_workspace_ids": connected_workspace_ids,
        "connected_workspace_count": len(connected_workspace_ids),
        "needs_second_connected_workspace": needs_second_workspace,
        "needs_live_flood_wait_isolation": needs_live_flood_wait,
        "keep_workspace_1_offline_unless_explicitly_enabled": needs_second_workspace,
    }


def _phase3_gateway_result_from_parts(
    *,
    local_result: dict,
    live_checks: list[dict],
    local_only: bool,
    workspace_id: int | None = None,
    approve_live_marker: str | None = None,
) -> dict:
    evaluated_live_checks = [
        check for check in live_checks if check.get("passed") is not None
    ]
    live_passed = (
        False
        if local_only
        else bool(evaluated_live_checks)
        and all(check.get("passed") is True for check in evaluated_live_checks)
        and len(evaluated_live_checks) == len(PHASE3_GATEWAY_REQUIRED_LIVE_GATES)
    )
    phase3_complete = bool(local_result["passed"] and live_passed)
    local_passed_count = sum(1 for check in local_result["checks"] if check["passed"])
    live_passed_count = sum(1 for check in evaluated_live_checks if check["passed"])
    result = {
        "passed": bool(local_result["passed"] if local_only else phase3_complete),
        "phase3_complete": phase3_complete,
        "scope": "local_only" if local_only else "full_live_gate",
        "summary": (
            f"local {local_passed_count}/{len(local_result['checks'])}; "
            + (
                "live not evaluated"
                if local_only
                else f"live {live_passed_count}/{len(PHASE3_GATEWAY_REQUIRED_LIVE_GATES)}"
            )
        ),
        "local": local_result,
        "live_gates": live_checks,
        "checks": [*local_result["checks"], *live_checks],
    }
    if not local_only and not phase3_complete:
        result["next_actions"] = _phase3_gateway_live_next_actions(live_checks)
        result["live_capture_plan"] = _phase3_gateway_live_capture_plan(
            live_checks,
            workspace_id=workspace_id,
            approve_live_marker=approve_live_marker,
        )
    return result


def _run_phase3_gateway_gate(
    *,
    local_only: bool,
    workspace_id: int | None,
    period_days: int,
    live_evidence_file: str | None,
) -> dict:
    local_result = _run_phase3_gateway_local_proofs()
    live_checks = _phase3_gateway_live_gate_checks(
        local_only=local_only,
        workspace_id=workspace_id,
        period_days=period_days,
        live_evidence_file=live_evidence_file,
    )
    return _phase3_gateway_result_from_parts(
        local_result=local_result,
        live_checks=live_checks,
        local_only=local_only,
        workspace_id=workspace_id,
    )


def _run_phase3_gateway_gate_until(
    *,
    local_only: bool,
    workspace_id: int | None,
    period_days: int,
    live_evidence_file: str | None,
    wait_seconds: int,
    poll_seconds: int,
    approve_live_marker: str | None = None,
) -> dict:
    local_result = _run_phase3_gateway_local_proofs()
    live_approval_attempt: dict | None = None
    live_approval_attempts = 0
    live_approval_terminal = False
    if (
        approve_live_marker
        and not local_only
        and local_result["passed"]
        and workspace_id is not None
    ):
        live_approval_attempt = _run_phase3_gateway_approve_latest_live_reply(
            workspace_id=workspace_id,
            period_days=period_days,
            trigger_message_marker=approve_live_marker,
        )
        live_approval_attempts += 1
        live_approval_terminal = bool(live_approval_attempt.get("attempted_send"))
    live_checks = _phase3_gateway_live_gate_checks(
        local_only=local_only,
        workspace_id=workspace_id,
        period_days=period_days,
        live_evidence_file=live_evidence_file,
    )
    result = _phase3_gateway_result_from_parts(
        local_result=local_result,
        live_checks=live_checks,
        local_only=local_only,
        workspace_id=workspace_id,
        approve_live_marker=approve_live_marker,
    )
    attempts = 1
    if local_only or not local_result["passed"] or wait_seconds <= 0 or result["phase3_complete"]:
        result["live_wait"] = {
            "attempts": attempts,
            "wait_seconds": wait_seconds,
            "poll_seconds": poll_seconds,
            "completed": bool(result["phase3_complete"]),
        }
        if live_approval_attempt is not None:
            result["live_approval"] = {
                **live_approval_attempt,
                "attempts": live_approval_attempts,
            }
        return result

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(min(max(1, poll_seconds), max(0, deadline - time.monotonic())))
        attempts += 1
        if approve_live_marker and workspace_id is not None and not live_approval_terminal:
            live_approval_attempt = _run_phase3_gateway_approve_latest_live_reply(
                workspace_id=workspace_id,
                period_days=period_days,
                trigger_message_marker=approve_live_marker,
            )
            live_approval_attempts += 1
            live_approval_terminal = bool(live_approval_attempt.get("attempted_send"))
        live_checks = _phase3_gateway_live_gate_checks(
            local_only=local_only,
            workspace_id=workspace_id,
            period_days=period_days,
            live_evidence_file=live_evidence_file,
        )
        result = _phase3_gateway_result_from_parts(
            local_result=local_result,
            live_checks=live_checks,
            local_only=local_only,
            workspace_id=workspace_id,
            approve_live_marker=approve_live_marker,
        )
        if result["phase3_complete"]:
            break

    result["live_wait"] = {
        "attempts": attempts,
        "wait_seconds": wait_seconds,
        "poll_seconds": poll_seconds,
        "completed": bool(result["phase3_complete"]),
    }
    if live_approval_attempt is not None:
        result["live_approval"] = {
            **live_approval_attempt,
            "attempts": live_approval_attempts,
        }
    return result


def _phase3_gateway_live_approval_option_error(
    *,
    approve_live_reply: bool,
    approve_live_marker: str | None,
    workspace_id: int | None,
    local_only: bool,
) -> str | None:
    if not approve_live_reply:
        return None
    if local_only:
        return "--approve-live-reply cannot be used with --local-only"
    if workspace_id is None:
        return "--approve-live-reply requires --workspace-id"
    marker = (approve_live_marker or "").strip()
    if not marker:
        return "--approve-live-reply requires --approve-live-marker"
    if len(marker) < 6:
        return "--approve-live-marker must be at least 6 characters"
    return None


def _run_phase3_gateway_approve_latest_live_reply(
    *,
    workspace_id: int,
    period_days: int,
    trigger_message_marker: str,
) -> dict:
    """Owner-approve one marker-guarded live reply through the existing delivery path."""
    script = f"""
import asyncio
import json
from datetime import datetime, timezone

async def main():
    from sqlalchemy import text
    from app.api.routes.ai_replies import (
        _apply_reply_delivery_result,
        _record_reply_delivery_started,
    )
    from app.core.config import get_settings
    from app.db.session import async_session
    from app.models.seller_agent_reply import SellerAgentReply, SellerAgentReplyStatus
    from app.services.delivery import DeliveryService
    from app.services.learning_signal_service import LearningSignalService

    workspace_id = {int(workspace_id)}
    period_days = {int(period_days)}
    marker = {json.dumps(trigger_message_marker.strip())}
    async with async_session() as session:
        row = (await session.execute(text(
            \"\"\"
            select
                ar.id as reply_id,
                ar.status as reply_status,
                ar.conversation_id,
                hr.run_id,
                hr.created_at as run_created_at,
                tm.content as trigger_message_content
            from ai_replies ar
            join conversations c on c.id = ar.conversation_id
            join messages tm on tm.id = ar.trigger_message_id
            join hermes_runs hr on hr.output_ref = 'seller_agent_reply:' || ar.id::text
            where c.workspace_id = :workspace_id
              and hr.trigger_type = 'telegram_message'
              and hr.details ? 'trigger_telemetry'
              and hr.created_at >= now() - (:period_days * interval '1 day')
              and ar.status in ('draft', 'delivery_failed')
              and position(lower(:marker) in lower(tm.content)) > 0
            order by ar.created_at desc
            limit 1
            \"\"\"
        ), {{
            "workspace_id": workspace_id,
            "period_days": period_days,
            "marker": marker,
        }})).mappings().one_or_none()
        if row is None:
            print(json.dumps({{
                "status": "no_matching_live_reply",
                "attempted_send": False,
                "workspace_id": workspace_id,
                "marker": marker,
                "detail": "no draft or failed live-telemetry reply matched the marker",
            }}))
            return

        reply = await session.get(SellerAgentReply, int(row["reply_id"]))
        if reply is None:
            print(json.dumps({{
                "status": "reply_not_found",
                "attempted_send": False,
                "workspace_id": workspace_id,
                "reply_id": int(row["reply_id"]),
            }}))
            return

        if reply.status == SellerAgentReplyStatus.SENT.value:
            print(json.dumps({{
                "status": "already_sent",
                "attempted_send": False,
                "workspace_id": workspace_id,
                "reply_id": reply.id,
                "run_id": row["run_id"],
            }}))
            return

        if reply.status not in {{
            SellerAgentReplyStatus.DRAFT.value,
            SellerAgentReplyStatus.DELIVERY_FAILED.value,
        }}:
            print(json.dumps({{
                "status": "not_pending",
                "attempted_send": False,
                "workspace_id": workspace_id,
                "reply_id": reply.id,
                "reply_status": reply.status,
                "run_id": row["run_id"],
            }}))
            return

        is_retry = reply.status == SellerAgentReplyStatus.DELIVERY_FAILED.value
        reply.status = SellerAgentReplyStatus.SENDING.value
        reply.final_content = reply.draft_content
        reply.reviewed_at = datetime.now(timezone.utc)
        reply.override_reason = "phase3_live_marker"
        reply.override_note = "Approved by oqim test phase3-gateway --approve-live-reply."
        await _record_reply_delivery_started(
            session,
            workspace_id=workspace_id,
            reply=reply,
            source="cli.phase3_gateway.live_approve",
        )
        if not is_retry:
            await LearningSignalService(session).record_approval(
                ai_reply_id=reply.id,
                workspace_id=workspace_id,
            )
        await session.commit()
        await session.refresh(reply)

        settings = get_settings()
        delivery = DeliveryService(
            sidecar_url=settings.sidecar_url,
            sidecar_api_key=settings.sidecar_api_key,
        )
        delivery_result = await delivery.deliver_message(
            reply.conversation_id,
            reply.final_content or reply.draft_content,
            db=session,
            workspace_id=workspace_id,
            ai_reply_id=reply.id,
            client_idempotency_key=f"phase3-live-approve:{{reply.id}}",
        )
        await _apply_reply_delivery_result(
            session,
            workspace_id=workspace_id,
            reply=reply,
            result=delivery_result,
            source="cli.phase3_gateway.live_approve",
        )
        await session.commit()
        await session.refresh(reply)
        print(json.dumps({{
            "status": "sent" if delivery_result.success else (delivery_result.state or "delivery_failed"),
            "attempted_send": True,
            "workspace_id": workspace_id,
            "reply_id": reply.id,
            "reply_status": reply.status,
            "run_id": row["run_id"],
            "external_message_id": delivery_result.external_message_id,
            "error": delivery_result.error,
        }}, default=str))

asyncio.run(main())
"""
    try:
        result = subprocess.run(
            [_venv_python(BACKEND_DIR), "-c", script],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "attempted_send": False,
            "workspace_id": workspace_id,
            "marker": trigger_message_marker,
            "detail": "live reply approval timed out",
            "output": str(exc),
        }
    output = result.stdout + result.stderr
    if result.returncode != 0:
        return {
            "status": "approval_unreachable",
            "attempted_send": False,
            "workspace_id": workspace_id,
            "marker": trigger_message_marker,
            "detail": "live reply approval failed before producing a delivery attempt",
            "output": _tail_output(output),
        }
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {
        "status": "invalid_output",
        "attempted_send": False,
        "workspace_id": workspace_id,
        "marker": trigger_message_marker,
        "detail": "live reply approval did not emit parseable JSON",
        "output": _tail_output(output),
    }


def _local_reality_steps(*, skip_browser: bool) -> list[dict]:
    steps = [
        {
            "name": "dependency-truth",
            "purpose": "backend, Postgres, Redis, and sidecar are reachable",
            "browser": False,
        },
        {
            "name": "api-capability",
            "purpose": "core APIs expose seeded conversations, messages, replies, media route, and dashboard",
            "browser": False,
        },
        {
            "name": "reconnect",
            "purpose": "reconnect/session helpers reconcile from canonical projection state",
            "browser": False,
        },
    ]
    if not skip_browser:
        steps.extend([
            {
                "name": "browser-cache-reset",
                "purpose": "stale browser cache/local state does not resurrect old data",
                "browser": True,
            },
            {
                "name": "app-smoke",
                "purpose": "real browser can login and open live conversation data",
                "browser": True,
            },
            {
                "name": "telegram-intake",
                "purpose": "GramJS-shaped webhook event reaches EventSpine, projections, reply queue, and browser smoke",
                "browser": True,
            },
        ])
    return steps


def _run_local_reality(
    *,
    skip_browser: bool = False,
    dry_run: bool = False,
    keep_fixture: bool = False,
) -> dict:
    planned_steps = _local_reality_steps(skip_browser=skip_browser)
    if dry_run:
        return {
            "passed": False,
            "dry_run": True,
            "summary": f"{len(planned_steps)} planned checks",
            "steps": [
                {
                    **step,
                    "passed": None,
                }
                for step in planned_steps
            ],
        }

    runners = {
        "dependency-truth": _run_dependency_truth_check,
        "api-capability": lambda: _run_api_capability_smoke(keep_fixture=keep_fixture),
        "reconnect": _run_reconnect_harness,
        "browser-cache-reset": _run_runtime_zero_browser_cache_proof,
        "app-smoke": lambda: _run_app_capability_smoke(keep_fixture=keep_fixture),
        "telegram-intake": lambda: _run_telegram_intake_harness(
            include_browser=True,
            keep_fixture=keep_fixture,
        ),
    }
    results = []
    for step in planned_steps:
        result = runners[step["name"]]()
        results.append({
            **step,
            "passed": result.get("passed", False),
            "summary": result.get("summary", "(no summary)"),
            "result": result,
        })
        if not result.get("passed", False):
            break

    passed = all(step["passed"] for step in results) and len(results) == len(planned_steps)
    return {
        "passed": passed,
        "dry_run": False,
        "summary": f"{sum(1 for step in results if step['passed'])}/{len(planned_steps)} checks passed",
        "steps": results,
        "remaining_steps": planned_steps[len(results):],
    }


def _pilot_gate_steps(*, workspaces: int, reply_workspace: int | None = None) -> list[dict]:
    reply_eval_command = (
        ["eval", "replies", "--workspace", str(reply_workspace), "--concurrency", "2", "--json"]
        if reply_workspace is not None
        else ["eval", "replies", "--seed-workspace", "--concurrency", "2", "--json"]
    )
    return [
        {
            "name": "runtime-zero",
            "command": ["test", "runtime-zero", "--reset", "--yes", "--cleanup-sidecar", "--json"],
            "purpose": "local DB/Redis/sidecar/browser state is reset and truthful before smoke",
        },
        {
            "name": "harness-parallel",
            "command": ["test", "harness-parallel", "--json"],
            "purpose": "DB-backed proof suites can run concurrently without racing on one test database",
        },
        {
            "name": "app-smoke",
            "command": ["test", "app-smoke", "--json"],
            "purpose": "real browser can login, open conversations, and render live backend data",
        },
        {
            "name": "telegram-intake",
            "command": ["test", "telegram-intake", "--browser", "--json"],
            "purpose": "GramJS-shaped webhook events reach EventSpine, projections, reply queue, and browser smoke",
        },
        {
            "name": "replay",
            "command": ["test", "replay", "--json"],
            "purpose": "canonical truth can rebuild projections",
        },
        {
            "name": "conversation-tail",
            "command": ["test", "conversation-tail", "--json"],
            "purpose": "list/detail/unread/tail state share canonical projection",
        },
        {
            "name": "delivery-chaos",
            "command": ["test", "delivery-chaos", "--json"],
            "purpose": "outbound sends survive retry, timeout, echo, and reclaim",
        },
        {
            "name": "media-chaos",
            "command": ["test", "media-chaos", "--json"],
            "purpose": "media states, retry, lease reclaim, and range streaming are explicit",
        },
        {
            "name": "reconnect",
            "command": ["test", "reconnect", "--json"],
            "purpose": "frontend reconnect equals canonical reload",
        },
        {
            "name": "onboarding-chaos",
            "command": ["test", "onboarding-chaos", "--json"],
            "purpose": "Telegram auth/session failures are recoverable state",
        },
        {
            "name": "embedding-chaos",
            "command": ["test", "embedding-chaos", "--json"],
            "purpose": "embedding/RAG failures degrade safely and stay tenant-scoped",
        },
        {
            "name": "tenants",
            "command": ["test", "tenants", "--workspaces", str(workspaces), "--json"],
            "purpose": "tenant fairness, source-learning isolation, storm isolation, DLQ, signals, and p95 guards pass",
        },
        {
            "name": "adapter-contract",
            "command": ["test", "adapter-contract", "--json"],
            "purpose": "Telegram and mocked Instagram share the adapter contract proof",
        },
        {
            "name": "reply-eval",
            "command": [
                *reply_eval_command[:-1],
                "--max-p95-ms",
                "45000",
                reply_eval_command[-1],
            ],
            "purpose": "seller reply quality golden eval passes for a selected or seeded workspace",
        },
        {
            "name": "sales-eval",
            "command": ["eval", "sales", "--json"],
            "purpose": "CRM stage, follow-up priority, and next-action eval passes",
        },
        {
            "name": "retrieval-core-eval",
            "command": ["eval", "retrieval-core", "--max-p95-ms", "5000", "--json"],
            "purpose": "Retrieval Core agentic RAG quality passes with p95 latency budget",
        },
        {
            "name": "rerank-provider-eval",
            "command": [
                "eval",
                "retrieval-core",
                "--live-rerank-provider",
                "--max-p95-ms",
                "5000",
                "--json",
            ],
            "purpose": "configured external reranker returns relevance scores instead of silent fallback",
        },
        {
            "name": "company-brain-eval",
            "command": ["eval", "company-brain", "--max-p95-ms", "10000", "--json"],
            "purpose": "Business Brain mixed-source learning and retrieval pass with source p95 budget",
        },
        {
            "name": "company-brain-live-eval",
            "command": [
                "eval",
                "company-brain",
                "--live",
                "--semantic",
                "--contextual-source-units",
                "--max-p95-ms",
                "60000",
                "--json",
            ],
            "purpose": "Live Gemini and Gemini embedding-2 source learning pass for messy mixed-source onboarding inputs",
        },
        {
            "name": "buyer-intent-eval",
            "command": [
                "eval",
                "buyer-intent",
                "--repetitions",
                "3",
                "--concurrency",
                "5",
                "--max-p95-ms",
                "5000",
                "--json",
            ],
            "purpose": "Universal Extraction buyer-intent quality passes under deterministic parallel pressure",
        },
        {
            "name": "runtime-audit",
            "command": ["audit", "runtime", "--json"],
            "purpose": "operator ledger has no partial or target blockers",
        },
    ]


def _run_pilot_gate(*, workspaces: int, dry_run: bool, reply_workspace: int | None = None) -> dict:
    steps = _pilot_gate_steps(workspaces=workspaces, reply_workspace=reply_workspace)
    if dry_run:
        return {
            "passed": False,
            "dry_run": True,
            "summary": f"{len(steps)} planned checks",
            "steps": [
                {
                    **step,
                    "argv": ["oqim", *step["command"]],
                    "passed": None,
                }
                for step in steps
            ],
        }

    results = []
    for step in steps:
        result = subprocess.run(
            [sys.executable, "-m", "cli", *step["command"]],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr
        results.append(
            {
                **step,
                "argv": ["oqim", *step["command"]],
                "passed": result.returncode == 0,
                "returncode": result.returncode,
                "summary": _parse_pytest_summary(output),
                "output_tail": _tail_output(output),
            }
        )
        if result.returncode != 0:
            break

    passed = all(step["passed"] for step in results) and len(results) == len(steps)
    return {
        "passed": passed,
        "dry_run": False,
        "summary": f"{sum(1 for step in results if step['passed'])}/{len(steps)} checks passed",
        "steps": results,
        "remaining_steps": steps[len(results):],
    }


def _tail_output(output: str, limit: int = 4000) -> str:
    if len(output) <= limit:
        return output
    return output[-limit:]


def _parse_pytest_summary(output: str) -> str:
    """Extract the last summary line from pytest output."""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if re.search(r"\d+ passed|\d+ failed|\d+ error", line):
            # Strip ANSI escape codes
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line)
            return clean.strip("= ").strip()
    return "(no summary)"


def _parse_vitest_summary(output: str) -> str:
    """Extract the summary line from vitest output."""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if re.search(r"Tests\s+\d+", line) or re.search(r"\d+ passed|\d+ failed", line, re.IGNORECASE):
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line)
            return clean.strip()
    return "(no summary)"


def _parse_node_test_summary(output: str) -> str:
    """Extract the pass/fail count from node:test TAP output."""
    tests = None
    passing = None
    failing = None
    for line in output.splitlines():
        tokens = line.strip().split()
        if not tokens:
            continue
        if tokens[0] not in {"tests", "pass", "fail"}:
            tokens = tokens[1:]
        if len(tokens) < 2:
            continue
        if tokens[0] == "tests":
            tests = tokens[1]
        elif tokens[0] == "pass":
            passing = tokens[1]
        elif tokens[0] == "fail":
            failing = tokens[1]
    if tests and passing and failing:
        return f"{passing}/{tests} passed, {failing} failed"
    return "(no summary)"


def _parse_playwright_summary(output: str) -> str:
    """Extract the summary line from Playwright output."""
    for line in reversed(output.splitlines()):
        clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        if re.search(r"\d+ (passed|failed|skipped)", clean):
            return clean
    return "(no summary)"


# ── Commands ──────────────────────────────────────────────────────────────────


@app.command()
def unit(
    service: Service = typer.Option(
        Service.all,
        "--service",
        "-s",
        help="Which service to test: backend, frontend, sidecar, all",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run unit tests for backend, frontend, and/or GramJS sidecar."""
    header("OQIM — Unit Tests")

    runners = {
        Service.backend: _run_backend_tests,
        Service.frontend: _run_frontend_tests,
        Service.sidecar: _run_sidecar_tests,
    }

    if service == Service.all:
        targets = [Service.backend, Service.frontend, Service.sidecar]
    else:
        targets = [service]

    results = []
    for svc in targets:
        if not json_mode:
            typer.echo(f"\n  Running {svc.value} tests...")
        result = runners[svc]()
        results.append(result)
        if not json_mode:
            status_line(f"{svc.value:<10}", result["passed"], result["summary"])

    all_passed = all(r["passed"] for r in results)

    if json_mode:
        typer.echo(json.dumps(
            {
                "passed": all_passed,
                "results": [
                    {"service": r["service"], "passed": r["passed"], "summary": r["summary"]}
                    for r in results
                ],
            },
            indent=2,
        ))
        raise typer.Exit(0 if all_passed else 1)

    typer.echo("")
    if all_passed:
        typer.echo(typer.style("  All tests passed.", fg=typer.colors.GREEN))
    else:
        typer.echo(typer.style("  Some tests failed.", fg=typer.colors.RED))
        # Print failing output
        for r in results:
            if not r["passed"]:
                typer.echo(f"\n  --- {r['service']} output ---")
                typer.echo(r["output"])

    raise typer.Exit(0 if all_passed else 1)


@app.command(name="run-evals")
def run_evals(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run AI quality evaluations (real LLM calls, slow)."""
    header("OQIM — AI Evals")

    if not json_mode:
        typer.echo("\n  Running evals/ with 120s timeout...")
        typer.echo("  (Uses real LLM calls — may take a few minutes)\n")

    result = subprocess.run(
        [
            _venv_python(BACKEND_DIR),
            "-m", "pytest",
            "evals/",
            "--timeout=120",
            "-v",
            "--tb=short",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )

    passed = result.returncode == 0
    summary = _parse_pytest_summary(result.stdout + result.stderr)

    if json_mode:
        typer.echo(json.dumps(
            {"passed": passed, "summary": summary, "output": result.stdout + result.stderr},
            indent=2,
        ))
        raise typer.Exit(0 if passed else 1)

    typer.echo(result.stdout)
    if result.stderr:
        typer.echo(result.stderr)

    typer.echo("")
    status_line("Evals", passed, summary)
    raise typer.Exit(0 if passed else 1)


@app.command(name="runtime-zero")
def runtime_zero(
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Destructively truncate local app tables and flush Redis before checking.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation when using --reset."),
    cleanup_sidecar: bool = typer.Option(
        False,
        "--cleanup-sidecar",
        help="Ask GramJS sidecar to drop workspace runtimes that are missing from Postgres.",
    ),
    browser: bool = typer.Option(
        False,
        "--browser",
        help="Also run the Playwright browser-cache resurrection proof.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove the local L0 runtime-zero state is clean and truthful."""
    if reset and not yes:
        if not typer.confirm(
            "This will TRUNCATE local app tables and FLUSH Redis db=0. Continue?",
            default=False,
        ):
            raise typer.Exit(1)

    if not json_mode:
        header("OQIM — Runtime Zero Harness")
        if reset:
            typer.echo("  Reset mode: database app tables will be truncated and Redis db=0 flushed.")
        else:
            typer.echo("  Check mode: no local state will be modified.")

    result = run_runtime_zero_sync(reset=reset, cleanup_sidecar=cleanup_sidecar)
    if browser:
        browser_check = _run_runtime_zero_browser_cache_proof()
        result["checks"].append(browser_check)
        result["passed"] = all(check["passed"] for check in result["checks"])
        result["summary"] = {
            "passed": sum(1 for check in result["checks"] if check["passed"]),
            "total": len(result["checks"]),
        }

    if json_mode:
        typer.echo(dumps_result(result))
        raise typer.Exit(0 if result["passed"] else 1)

    for check in result["checks"]:
        status_line(check["name"], check["passed"], check["detail"])
        data = check.get("data") or {}
        if check["name"] == "database_zero" and data.get("nonzero_tables"):
            typer.echo(f"     nonzero tables: {data['nonzero_tables']}")
        if check["name"] == "redis_zero" and data.get("key_count"):
            typer.echo(f"     redis key count: {data['key_count']}")
            typer.echo(f"     sample keys: {data.get('keys', [])}")
        if check["name"] == "sidecar_stale_workspaces" and data.get("stale"):
            typer.echo(f"     stale sidecar sessions: {data['stale']}")
        if check["name"] == "browser_cache_reset":
            typer.echo(f"     {data.get('summary', '(no summary)')}")
            if not check["passed"] and data.get("output"):
                typer.echo(data["output"])

    typer.echo("")
    summary = result["summary"]
    status_line(
        "runtime_zero",
        result["passed"],
        f"{summary['passed']}/{summary['total']} checks passed",
    )

    if not result["passed"]:
        typer.echo("")
        typer.echo("  To clean local state intentionally, run:")
        typer.echo("  oqim test runtime-zero --reset --cleanup-sidecar --yes")

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="app-smoke")
def app_smoke(
    keep_fixture: bool = typer.Option(
        False,
        "--keep-fixture",
        help="Keep the seeded smoke workspace after a successful run.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove the real browser can login and open canonical conversation data."""
    if not json_mode:
        header("OQIM — App Capability Smoke")
        typer.echo("  Seeding one local seller workspace and opening the real app in Playwright...")

    result = _run_app_capability_smoke(keep_fixture=keep_fixture)

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    fixture = result.get("fixture") or {}
    status_line("app_smoke", result["passed"], result["summary"])
    if fixture:
        typer.echo(
            f"     workspace={fixture.get('workspace_id')} conversation={fixture.get('conversation_id')} kept={fixture.get('kept')}"
        )
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="fake-telegram-smoke")
def fake_telegram_smoke(
    keep_fixture: bool = typer.Option(
        False,
        "--keep-fixture",
        help="Keep the seeded fake Telegram workspace after a successful run.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove fake GramJS webhook events reach the real browser through canonical state."""
    if not json_mode:
        header("OQIM — Fake Telegram Runtime Smoke")
        typer.echo(
            "  Seeding a Telegram-connected fake seller and injecting a GramJS-shaped webhook..."
        )

    result = _run_fake_telegram_smoke(keep_fixture=keep_fixture)

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    fixture = result.get("fixture") or {}
    status_line("fake_telegram_smoke", result["passed"], result["summary"])
    if fixture:
        typer.echo(
            f"     workspace={fixture.get('workspace_id')} conversation={fixture.get('conversation_id')} kept={fixture.get('kept')}"
        )
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="telegram-intake")
def telegram_intake(
    browser: bool = typer.Option(
        False,
        "--browser",
        help="Also run the seeded Playwright fake Telegram smoke against the local app.",
    ),
    keep_fixture: bool = typer.Option(
        False,
        "--keep-fixture",
        help="Keep the seeded browser-smoke workspace after a successful --browser run.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove Telegram intake from GramJS-shaped events to projections and reply queue."""
    if not json_mode:
        header("OQIM — Telegram Intake Harness")
        typer.echo(
            "  Running authoritative EventSpine intake, GramJS sidecar contract, and optional browser proof..."
        )

    result = _run_telegram_intake_harness(
        include_browser=browser,
        keep_fixture=keep_fixture,
    )

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    for check in result["checks"]:
        status_line(check["name"], check["passed"], check["summary"])
    if not browser:
        status_line("browser_fake_telegram_smoke", True, "skipped; pass --browser for UI proof")
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="live-telegram-onboarding")
def live_telegram_onboarding(
    workspace_id: Optional[int] = typer.Option(
        None,
        "--workspace-id",
        help="Connected Telegram workspace id. Defaults to the first connected sidecar workspace.",
    ),
    channel: Optional[str] = typer.Option(
        None,
        "--channel",
        help="Channel id, username, handle, or name. Defaults to the first owned channel, then first channel.",
    ),
    limit: int = typer.Option(30, "--limit", min=1, max=300, help="How many channel posts to read."),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Read live Telegram channel posts and prove they are usable as onboarding source learning input."""
    if not json_mode:
        header("OQIM — Live Telegram Onboarding Proof")
        typer.echo("  Reading live Telegram channels through GramJS without sending messages...")

    result = _run_live_telegram_onboarding_probe(
        workspace_id=workspace_id,
        channel=channel,
        limit=limit,
    )

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    for check in result["checks"]:
        status_line(check["name"], check["passed"], check["detail"])
    source_item = result.get("source_item") or {}
    if source_item:
        typer.echo(
            f"     source={source_item.get('label')} messages={len(source_item.get('messages') or [])}"
        )
    if result.get("blocked") and result.get("next_actions"):
        typer.echo("  Next actions:")
        for action in result["next_actions"]:
            typer.echo(f"   - {action}")
    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="admin-runtime-smoke")
def admin_runtime_smoke(
    keep_fixture: bool = typer.Option(
        False,
        "--keep-fixture",
        help="Keep the seeded admin workspace after a successful run.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove a founder can open the runtime console in the real browser."""
    if not json_mode:
        header("OQIM — Founder Runtime Smoke")
        typer.echo("  Opening /founder/runtime with an allowlisted founder smoke account...")

    result = _run_admin_runtime_smoke(keep_fixture=keep_fixture)

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    fixture = result.get("fixture") or {}
    status_line("admin_runtime_smoke", result["passed"], result["summary"])
    if fixture:
        typer.echo(
            f"     workspace={fixture.get('workspace_id')} conversation={fixture.get('conversation_id')} kept={fixture.get('kept')}"
        )
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="full-browser-smoke")
def full_browser_smoke(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run seller-side browser smokes first, then founder-side runtime smoke."""
    if not json_mode:
        header("OQIM — Full Browser Smoke")
        typer.echo("  1) seller app, 2) seller fake Telegram update, 3) founder runtime console")

    result = _run_full_browser_smoke()

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    for step in result["steps"]:
        step_result = step["result"]
        status_line(step["name"], step_result["passed"], step_result["summary"])
        if not step_result["passed"]:
            typer.echo("")
            typer.echo(step_result.get("output", ""))
            break

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="replay")
def replay(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove canonical EventSpine replay rebuilds conversation projections."""
    if not json_mode:
        header("OQIM — Replay Harness")
        typer.echo("  Running canonical EventSpine replay projection proof...")

    result = _run_replay_harness()

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    status_line("replay", result["passed"], result["summary"])
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="conversation-tail")
def conversation_tail(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove conversation tail/list/read state does not depend on route-time repair."""
    if not json_mode:
        header("OQIM — Conversation Tail Harness")
        typer.echo("  Running canonical conversation tail projection proof...")

    result = _run_conversation_tail_harness()

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    status_line("conversation_tail", result["passed"], result["summary"])
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="live-chat-truth")
def live_chat_truth(
    workspace_id: int = typer.Option(..., "--workspace-id", help="Local workspace id to dev-login as."),
    conversation_id: Optional[int] = typer.Option(
        None,
        "--conversation-id",
        help="Conversation to prove. Defaults to the first renderable conversation in the list.",
    ),
    scan_limit: int = typer.Option(20, "--scan-limit", help="Conversation list size to scan when no id is provided."),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove a real local chat list/detail/messages route renders canonical messages in a browser."""
    if not json_mode:
        header("OQIM — Live Chat Truth")
        typer.echo("  Opening a real local workspace through dev-login and proving chat messages render...")

    result = _run_live_chat_truth_smoke(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        scan_limit=scan_limit,
    )

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    status_line("live_chat_truth", result["passed"], result["summary"])
    fixture = result.get("fixture") or {}
    if fixture:
        typer.echo(
            f"     workspace={fixture.get('workspace_id')} conversation={fixture.get('conversation_id')} "
            f"messages={fixture.get('message_count')} screenshot={result.get('screenshot')}"
        )
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="delivery-chaos")
def delivery_chaos(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove sends are idempotent and uncertain delivery is recoverable."""
    if not json_mode:
        header("OQIM — Delivery Chaos Harness")
        typer.echo("  Running delivery idempotency, unknown, echo, and reclaim proof...")

    result = _run_delivery_chaos_harness()

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    status_line("delivery_chaos", result["passed"], result["summary"])
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="media-chaos")
def media_chaos(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove media state, retry, lease, and streaming behavior is explicit."""
    if not json_mode:
        header("OQIM — Media Chaos Harness")
        typer.echo("  Running media action state, retry, lease, and range-stream proof...")

    result = _run_media_chaos_harness()

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    status_line("media_chaos", result["passed"], result["summary"])
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="reconnect")
def reconnect(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove reconnect reconciles from canonical sync-session state."""
    if not json_mode:
        header("OQIM — Reconnect Harness")
        typer.echo("  Running backend sync-session and frontend reconnect equivalence proof...")

    result = _run_reconnect_harness()

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    status_line("reconnect", result["passed"], result["summary"])
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="onboarding-chaos")
def onboarding_chaos(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove Telegram onboarding/session failures are recoverable state."""
    if not json_mode:
        header("OQIM — Onboarding Chaos Harness")
        typer.echo("  Running Telegram session, 2FA, QR, frontend, and sidecar boundary proofs...")

    result = _run_onboarding_chaos_harness()

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    status_line("onboarding_chaos", result["passed"], result["summary"])
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="embedding-chaos")
def embedding_chaos(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove embeddings/RAG degrade safely and stay tenant-scoped."""
    if not json_mode:
        header("OQIM — Embedding Chaos Harness")
        typer.echo("  Running embedding quota, dimension, idempotency, fallback, and tenant proofs...")

    result = _run_embedding_chaos_harness()

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    status_line("embedding_chaos", result["passed"], result["summary"])
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="tenants")
def tenants(
    workspaces: int = typer.Option(1000, "--workspaces", help="Synthetic workspace count"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove deterministic multi-tenant isolation and noisy-neighbor behavior."""
    if not json_mode:
        header("OQIM — Tenant Harness")
        typer.echo(
            f"  Running workspace isolation, queue fairness, noisy-neighbor, DLQ, signals, and p95 proof for {workspaces} tenants..."
        )

    result = _run_tenant_harness(workspaces=workspaces)

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    status_line("tenants", result["passed"], result["summary"])
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="adapter-contract")
def adapter_contract(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove channel adapter, persistence, and delivery replay contracts."""
    if not json_mode:
        header("OQIM — Adapter Contract Harness")
        typer.echo("  Running channel contract, agnostic persistence, and delivery replay proofs...")

    result = _run_adapter_contract_harness()

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    status_line("adapter_contract", result["passed"], result["summary"])
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="harness-parallel")
def harness_parallel(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove DB-backed harnesses can run concurrently without test DB races."""
    if not json_mode:
        header("OQIM — Parallel Harness Truth")
        typer.echo("  Running DB-backed chaos suites concurrently with isolated test databases...")

    result = _run_harness_parallel_harness()

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    status_line("harness_parallel", result["passed"], result["summary"])
    if not result["passed"]:
        typer.echo("")
        typer.echo(result["output"])

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="phase3-gateway")
def phase3_gateway(
    local_only: bool = typer.Option(
        False,
        "--local-only",
        help="Run local contract proofs only; live Phase 3 gates are reported as not evaluated.",
    ),
    workspace_id: Optional[int] = typer.Option(
        None,
        "--workspace-id",
        help=(
            "Workspace id used when reading live runtime-signals for the p50 gate. "
            "Omit to use the most recently updated Telegram-connected workspace."
        ),
    ),
    period_days: int = typer.Option(
        7,
        "--period-days",
        min=1,
        max=30,
        help="Runtime-signals lookback window for the live trigger-start p50 gate.",
    ),
    live_evidence_file: Optional[str] = typer.Option(
        None,
        "--live-evidence-file",
        help="Optional JSON file containing manually recorded live gate evidence keyed by gate name.",
    ),
    live_wait_seconds: int = typer.Option(
        0,
        "--live-wait-seconds",
        min=0,
        max=3600,
        help="Poll live gates for this many seconds after the local contracts pass.",
    ),
    live_poll_seconds: int = typer.Option(
        5,
        "--live-poll-seconds",
        min=1,
        max=60,
        help="Polling interval used with --live-wait-seconds.",
    ),
    approve_live_reply: bool = typer.Option(
        False,
        "--approve-live-reply",
        help=(
            "Explicitly approve and send one marker-matched live draft reply "
            "while polling. Requires --workspace-id and --approve-live-marker."
        ),
    ),
    approve_live_marker: Optional[str] = typer.Option(
        None,
        "--approve-live-marker",
        help=(
            "Marker text that must appear in the live trigger message before "
            "--approve-live-reply can send anything."
        ),
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Audit the Phase 3 Telegram gateway gate without inventing missing live proof."""
    approval_option_error = _phase3_gateway_live_approval_option_error(
        approve_live_reply=approve_live_reply,
        approve_live_marker=approve_live_marker,
        workspace_id=workspace_id,
        local_only=local_only,
    )
    if approval_option_error:
        raise typer.BadParameter(approval_option_error)

    if not json_mode:
        header("OQIM — Phase 3 Gateway Gate")
        if local_only:
            typer.echo("  Running local contract proof; live gates will be listed as not evaluated.")
        else:
            typer.echo("  Running local contracts and checking required live Phase 3 evidence.")
        if live_wait_seconds > 0:
            typer.echo(
                f"  Waiting up to {live_wait_seconds}s for live gates "
                f"(poll {live_poll_seconds}s)."
            )
        if approve_live_reply:
            typer.echo(
                "  Live reply approval enabled for marker: "
                f"{approve_live_marker.strip() if approve_live_marker else ''}"
            )

    resolved_workspace_id = workspace_id
    if live_wait_seconds > 0:
        result = _run_phase3_gateway_gate_until(
            local_only=local_only,
            workspace_id=resolved_workspace_id,
            period_days=period_days,
            live_evidence_file=live_evidence_file,
            wait_seconds=live_wait_seconds,
            poll_seconds=live_poll_seconds,
            approve_live_marker=(
                approve_live_marker.strip()
                if approve_live_reply and approve_live_marker
                else None
            ),
        )
    else:
        live_approval = None
        if approve_live_reply and approve_live_marker and resolved_workspace_id is not None:
            live_approval = _run_phase3_gateway_approve_latest_live_reply(
                workspace_id=resolved_workspace_id,
                period_days=period_days,
                trigger_message_marker=approve_live_marker.strip(),
            )
        result = _run_phase3_gateway_gate(
            local_only=local_only,
            workspace_id=resolved_workspace_id,
            period_days=period_days,
            live_evidence_file=live_evidence_file,
        )
        if live_approval is not None:
            result["live_approval"] = {
                **live_approval,
                "attempts": 1,
            }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if result["passed"] else 1)

    rows = [
        [
            check["name"],
            check.get("status") or ("pass" if check.get("passed") else "fail"),
            check.get("detail", ""),
        ]
        for check in result["checks"]
    ]
    table(["check", "status", "detail"], rows)
    typer.echo("")
    status_line("phase3_gateway", result["passed"], result["summary"])
    if result.get("live_wait"):
        wait = result["live_wait"]
        typer.echo(
            f"  Live wait attempts: {wait.get('attempts')} "
            f"(completed={wait.get('completed')})"
        )
    if result.get("live_approval"):
        approval = result["live_approval"]
        typer.echo(
            "  Live approval: "
            f"status={approval.get('status')} "
            f"attempted_send={approval.get('attempted_send')} "
            f"attempts={approval.get('attempts')}"
        )
    if local_only and result["passed"]:
        typer.echo("  Local contracts passed, but Phase 3 is not complete until live gates pass.")
    if result.get("next_actions"):
        typer.echo("")
        typer.echo("Next live proof actions:")
        for action in result["next_actions"]:
            typer.echo(f"  - {action}")
    if result.get("live_capture_plan"):
        plan = result["live_capture_plan"]
        typer.echo("")
        typer.echo("Live capture plan:")
        if plan.get("required_live_messages"):
            typer.echo(
                "  - Send "
                f"{plan['required_live_messages']} messages from {plan['send_from']}."
            )
        if plan.get("message_marker"):
            typer.echo(f"  - Include marker: {plan['message_marker']}")
        if plan.get("needs_second_connected_workspace"):
            typer.echo("  - Connect a second Telegram workspace for isolation proof.")
        typer.echo(f"  - Run: {plan['wait_command']}")
    if not result["passed"]:
        failed_output = "\n".join(
            check.get("output", "")
            for check in result["checks"]
            if check.get("passed") is False and check.get("output")
        )
        if failed_output:
            typer.echo("")
            typer.echo(_tail_output(failed_output))

    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="phase4-knowledge")
def phase4_knowledge(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Audit the local Phase 4 Knowledge MCP and Agent Control proof gate."""
    if not json_mode:
        header("OQIM — Phase 4 Knowledge MCP Gate")
        typer.echo("  Running local Knowledge MCP, Chat Memory, Agent Control, and Hermes tool proofs.")

    result = _run_phase4_knowledge_local_proofs()

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    for check in result["checks"]:
        status_line(check["name"], check["passed"], check["detail"])
        typer.echo(f"     proof paths: {', '.join(check.get('proof_paths', []))}")
        if not check["passed"] and check.get("output"):
            typer.echo(check["output"])

    typer.echo("")
    status_line("phase4_knowledge", result["passed"], result["summary"])
    raise typer.Exit(0 if result["passed"] else 1)


@app.command(name="local-reality")
def local_reality(
    skip_browser: bool = typer.Option(
        False,
        "--skip-browser",
        help="Run only dependency/API/reconnect checks; omit Playwright browser smokes.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned checks without running them."),
    keep_fixture: bool = typer.Option(
        False,
        "--keep-fixture",
        help="Keep seeded smoke workspaces after successful API/browser checks.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Prove the local app is actually testable before manual smoke."""
    if not json_mode:
        header("OQIM — Local Reality Harness")
        if dry_run:
            typer.echo("  Dry run: showing the checks without executing them.")
        else:
            typer.echo("  Running live dependency, API, reconnect, and browser truth checks...")

    result = _run_local_reality(
        skip_browser=skip_browser,
        dry_run=dry_run,
        keep_fixture=keep_fixture,
    )

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] or dry_run else 1)

    rows = [
        [
            step["name"],
            "planned" if step.get("passed") is None else ("pass" if step["passed"] else "fail"),
            step["purpose"],
        ]
        for step in result["steps"]
    ]
    rows.extend(
        [
            [step["name"], "pending", step["purpose"]]
            for step in result.get("remaining_steps", [])
        ]
    )
    table(["check", "status", "purpose"], rows)
    typer.echo("")
    status_line("local_reality", result["passed"], result["summary"])

    if result["steps"] and result["steps"][-1].get("passed") is False:
        failed = result["steps"][-1]
        failed_result = failed.get("result") or {}
        typer.echo("")
        typer.echo(f"  Failed check: {failed['name']}")
        if failed_result.get("checks"):
            for check in failed_result["checks"]:
                status_line(check["name"], check.get("passed", False), check.get("detail", ""))
        output = failed_result.get("output")
        if output:
            typer.echo("")
            typer.echo(output)

    raise typer.Exit(0 if result["passed"] or dry_run else 1)


@app.command(name="pilot-gate")
def pilot_gate(
    workspaces: int = typer.Option(1000, "--workspaces", help="Synthetic workspace count for tenant proof"),
    reply_workspace: int | None = typer.Option(
        None,
        "--reply-workspace",
        help="Workspace ID for live reply-quality eval; omit to use the seeded eval workspace",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the gate plan without running checks"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Compose the proof harnesses into one pilot-readiness gate."""
    if not json_mode:
        header("OQIM — Pilot Gate")
        if dry_run:
            typer.echo("  Dry run: showing required checks without executing them.")
        else:
            typer.echo("  Running deterministic runtime, chaos, eval, tenant, adapter, and audit gates...")

    result = _run_pilot_gate(
        workspaces=workspaces,
        dry_run=dry_run,
        reply_workspace=reply_workspace,
    )

    if json_mode:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result["passed"] else 1)

    rows = [
        [
            step["name"],
            "planned" if step.get("passed") is None else ("pass" if step["passed"] else "fail"),
            "oqim " + " ".join(step["command"]),
        ]
        for step in result["steps"]
    ]
    rows.extend(
        [
            [step["name"], "pending", "oqim " + " ".join(step["command"])]
            for step in result.get("remaining_steps", [])
        ]
    )
    table(["check", "status", "command"], rows)
    typer.echo("")
    status_line("pilot_gate", result["passed"], result["summary"])
    if result["steps"] and result["steps"][-1].get("passed") is False:
        typer.echo("")
        typer.echo(result["steps"][-1].get("output_tail", ""))

    raise typer.Exit(0 if result["passed"] else 1)


@app.command()
def mine(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Export learning_signals corrections to JSONL for eval datasets."""
    header("OQIM — Mine Corrections")

    script = BACKEND_DIR / "evals" / "scripts" / "mine_corrections.py"
    if not script.exists():
        typer.echo(f"  Script not found: {script}")
        raise typer.Exit(1)

    if not json_mode:
        typer.echo("\n  Running mine_corrections.py...")

    result = subprocess.run(
        [_venv_python(BACKEND_DIR), str(script)],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )

    if json_mode:
        # Try to find a count in the output
        count = _extract_corrections_count(result.stdout + result.stderr)
        typer.echo(json.dumps(
            {
                "passed": result.returncode == 0,
                "corrections_exported": count,
                "output": result.stdout + result.stderr,
            },
            indent=2,
        ))
        raise typer.Exit(result.returncode)

    typer.echo(result.stdout)
    if result.stderr:
        typer.echo(result.stderr)

    count = _extract_corrections_count(result.stdout + result.stderr)
    status_line(
        "mine_corrections",
        result.returncode == 0,
        f"{count} corrections exported" if count is not None else "",
    )
    raise typer.Exit(result.returncode)


def _extract_corrections_count(output: str) -> Optional[int]:
    """Try to parse a corrections count from script output."""
    for pattern in [
        r"(\d+)\s+corrections? exported",
        r"exported\s+(\d+)",
        r"total[:\s]+(\d+)",
        r"wrote\s+(\d+)",
    ]:
        m = re.search(pattern, output, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


@app.command()
def tokens(
    days: int = typer.Option(7, "--days", help="Number of days to look back"),
    workspace: int = typer.Option(1, "--workspace", help="Workspace ID"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show token usage breakdown from Redis (last N days, per workspace)."""
    import asyncio

    asyncio.run(_tokens_impl(days=days, workspace=workspace, json_mode=json_mode))


async def _tokens_impl(days: int, workspace: int, json_mode: bool) -> None:
    try:
        import redis.asyncio as aioredis
    except ImportError:
        typer.echo("  redis-py not installed. Run: pip install redis")
        raise typer.Exit(1)

    redis_url = f"redis://localhost:{PORTS['redis']}/0"

    if not json_mode:
        header(f"Token Usage — workspace {workspace}, last {days} days")

    try:
        client = aioredis.from_url(redis_url, decode_responses=True)
        await client.ping()
    except Exception as e:
        typer.echo(f"  Cannot connect to Redis: {e}")
        raise typer.Exit(1)

    today = date.today()
    date_range = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]

    rows = []
    all_operations: set[str] = set()

    # First pass: collect all keys and operations
    day_data: dict[str, dict[str, int]] = {}
    for d in date_range:
        key = f"tokens:{workspace}:{d}"
        try:
            data = await client.hgetall(key)
        except Exception:
            data = {}
        day_data[d] = {op: int(v) for op, v in data.items()}
        all_operations.update(day_data[d].keys())

    await client.aclose()

    operations = sorted(all_operations)

    if not operations:
        if json_mode:
            typer.echo(json.dumps({"workspace": workspace, "days": days, "rows": []}, indent=2))
        else:
            typer.echo("\n  No token data found for this workspace/date range.")
        return

    # Build rows: date | op1 | op2 | ... | total
    headers = ["date"] + operations + ["total"]
    for d in date_range:
        data = day_data[d]
        total = sum(data.values())
        if total == 0 and not data:
            continue  # skip empty days
        row = [d] + [data.get(op, 0) for op in operations] + [total]
        rows.append(row)

    if json_mode:
        typer.echo(json.dumps(
            {
                "workspace": workspace,
                "days": days,
                "headers": headers,
                "rows": [dict(zip(headers, r)) for r in rows],
            },
            indent=2,
        ))
        return

    if not rows:
        typer.echo("\n  No token data found for this workspace/date range.")
        return

    typer.echo("")
    table(headers, rows, json_mode=False)

    grand_total = sum(r[-1] for r in rows)
    typer.echo(f"\n  Total ({days}d): {grand_total:,} tokens")


@app.command()
def check(
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Check staged files for banned patterns (pre-commit style)."""
    header("OQIM — Banned Pattern Check")

    script = PROJECT_ROOT / "scripts" / "check_banned_patterns.sh"
    if not script.exists():
        typer.echo(f"  Script not found: {script}")
        if json_mode:
            typer.echo(json.dumps({"passed": False, "error": "script not found"}, indent=2))
        raise typer.Exit(1)

    if not json_mode:
        typer.echo("\n  Running check_banned_patterns.sh...")

    result = subprocess.run(
        ["bash", str(script)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    passed = result.returncode == 0

    if json_mode:
        typer.echo(json.dumps(
            {
                "passed": passed,
                "output": result.stdout + result.stderr,
            },
            indent=2,
        ))
        raise typer.Exit(0 if passed else 1)

    if result.stdout:
        typer.echo(result.stdout)
    if result.stderr:
        typer.echo(result.stderr)

    typer.echo("")
    status_line("Banned pattern check", passed)
    raise typer.Exit(0 if passed else 1)


# ── Retired Real E2E ────────────────────────────────────


@app.command()
def e2e_setup():
    """Retired: old real E2E sender setup."""
    typer.echo(
        "Retired: evals/e2e_real.py used the old channel transport path. "
        "Use canonical harness commands like `oqim test runtime-zero`, "
        "`oqim test replay`, and browser smoke instead."
    )
    raise typer.Exit(2)


@app.command()
def e2e():
    """Retired: old real E2E sender."""
    typer.echo(
        "Retired: this command sent real Telegram messages through the old "
        "channel transport path. Use canonical runtime/browser harnesses instead."
    )
    raise typer.Exit(2)
