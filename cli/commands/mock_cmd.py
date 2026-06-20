"""oqim mock — local mock helpers for canonical EventSpine development."""
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import typer

from cli.config import LOG_DIR

app = typer.Typer(no_args_is_help=True)

_PID_FILE = LOG_DIR / "mock.pid"


def _read_pid() -> int | None:
    if _PID_FILE.exists():
        try:
            return int(_PID_FILE.read_text().strip())
        except (ValueError, OSError):
            pass
    return None


def _clear_pid() -> None:
    if _PID_FILE.exists():
        _PID_FILE.unlink()


@app.command()
def start() -> None:
    """Deprecated: canonical mock intake no longer starts a fixture server."""
    typer.echo(
        typer.style(
            "  x  Mock fixture start is retired. Use `oqim mock send \"salom\"` to append canonical EventSpine events.",
            fg=typer.colors.RED,
        )
    )
    raise typer.Exit(1)


@app.command()
def stop() -> None:
    """Stop a previously-started legacy mock fixture, if a stale PID exists."""
    pid = _read_pid()

    if pid is None:
        typer.echo(typer.style("  -  No legacy mock fixture PID found.", fg=typer.colors.YELLOW))
        raise typer.Exit(0)

    try:
        os.kill(pid, signal.SIGTERM)
        typer.echo(typer.style(f"  +  Legacy mock fixture (PID {pid}) stopped.", fg=typer.colors.GREEN))
    except ProcessLookupError:
        typer.echo(typer.style(f"  -  PID {pid} not running (already stopped).", fg=typer.colors.YELLOW))
    except PermissionError:
        typer.echo(typer.style(f"  x  Permission denied killing PID {pid}.", fg=typer.colors.RED))
        raise typer.Exit(1)
    finally:
        _clear_pid()


@app.command()
def send(
    message: str = typer.Argument(..., help="Message text to append as a canonical EventSpine event"),
    from_chat: int = typer.Option(1001, "--from", "-f", help="Telegram chat_id of the sender"),
    workspace: int = typer.Option(1, "--workspace", "-w", help="Target workspace ID"),
) -> None:
    """Append a fake inbound message to the canonical workspace EventSpine."""
    try:
        import redis as sync_redis  # type: ignore
    except ImportError:
        typer.echo(typer.style("  x  redis-py is not installed. Run: pip install redis", fg=typer.colors.RED))
        raise typer.Exit(1)

    # Resolve display name from dialogs fixture
    fixtures_path = Path(__file__).parent.parent / "mock" / "fixtures" / "dialogs.json"
    display_name = "Test Customer"
    if fixtures_path.exists():
        try:
            dialogs = json.loads(fixtures_path.read_text(encoding="utf-8"))
            for d in dialogs:
                if d.get("telegram_chat_id") == from_chat:
                    display_name = d.get("display_name", display_name)
                    break
        except (json.JSONDecodeError, OSError):
            pass

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6381/0")

    try:
        r = sync_redis.from_url(redis_url, decode_responses=True)
        r.ping()
    except Exception as exc:
        typer.echo(typer.style(f"  x  Cannot connect to Redis at {redis_url}: {exc}", fg=typer.colors.RED))
        raise typer.Exit(1)

    stream_key = f"oqim:events:{workspace}"
    now = datetime.now(timezone.utc).timestamp()
    message_id = int(time.time() * 1000)
    event = {
        "type": "msg.inbound",
        "schema_version": 1,
        "event_id": uuid4().hex,
        "workspace_id": workspace,
        "channel": "telegram_dm",
        "channel_account_id": "mock",
        "channel_conversation_id": str(from_chat),
        "channel_message_id": str(message_id),
        "correlation_id": f"mock:{uuid4().hex}",
        "causation_id": None,
        "occurred_at": now,
        "received_at": now,
        "emitted_at": now,
        "idempotency_key": f"mock:tg:{from_chat}:{message_id}",
        "telegram_chat_id": from_chat,
        "telegram_message_id": message_id,
        "sender_telegram_id": from_chat,
        "is_outgoing": False,
        "text": message,
        "media_type": None,
        "media_metadata": None,
        "text_entities": None,
        "reply_to_msg_id": None,
        "forward_from_name": None,
        "forward_date": None,
        "grouped_id": None,
        "sent_at": now,
    }

    payload = {
        "schema_version": "1",
        "event_id": event["event_id"],
        "type": event["type"],
        "workspace_id": str(workspace),
        "channel": "telegram_dm",
        "channel_account_id": "mock",
        "channel_conversation_id": str(from_chat),
        "channel_message_id": str(message_id),
        "idempotency_key": event["idempotency_key"],
        "correlation_id": event["correlation_id"],
        "causation_id": "",
        "occurred_at": str(now),
        "received_at": str(now),
        "payload": json.dumps(event),
    }

    msg_id = r.xadd(stream_key, payload, maxlen=10000, approximate=True)

    typer.echo(typer.style(f"  +  Message pushed to {stream_key}", fg=typer.colors.GREEN))
    typer.echo(f"     Stream ID : {msg_id}")
    typer.echo(f"     From      : {display_name} (chat_id={from_chat})")
    typer.echo(f"     Workspace : {workspace}")
    typer.echo(f'     Text      : "{message}"')
