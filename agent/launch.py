"""Run all four local services; Ctrl+C terminates only this launcher's children."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main():
    load_dotenv(ROOT / ".env", override=False)
    ports = []
    for variable, default in (
        ("ENGINE_URL", "http://localhost:8000"),
        ("MCP_UPSTREAM_URL", "http://localhost:8100/mcp"),
        ("MCP_GATEWAY_URL", "http://localhost:8200/mcp"),
    ):
        url = urlsplit(os.getenv(variable, default))
        if (
            url.hostname not in {"localhost", "127.0.0.1"}
            or url.scheme != "http"
            or not url.port
        ):
            raise SystemExit(
                f"The local launcher requires {variable} to use HTTP, localhost, and an explicit port"
            )
        ports.append(url.port)
    ports.append(8501)
    if len(set(ports)) != len(ports):
        raise SystemExit("Each local service needs a different port")
    for port in ports:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(
                    f"Port {port} is in use. Stop the existing service before launching"
                ) from None
    commands = [
        (
            "engine",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "engine.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(ports[0]),
            ],
        ),
        ("tools", [sys.executable, "-m", "mcp_tools.server"]),
        ("gateway", [sys.executable, "-m", "gateway.server"]),
        (
            "dashboard",
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "dashboard/app.py",
                "--server.port",
                "8501",
                "--server.headless",
                "true",
            ],
        ),
    ]
    logs = ROOT / "work/logs"
    logs.mkdir(parents=True, exist_ok=True)
    children, handles = [], []
    env = {
        **os.environ,
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
    }
    try:
        for (name, command), port in zip(commands, ports):
            handle = (logs / f"{name}.log").open("w", encoding="utf-8")
            handles.append(handle)
            child = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            children.append(child)
            deadline = time.monotonic() + 30
            while True:
                if child.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(
                        f"{name} did not start; inspect work/logs/{name}.log"
                    )
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
        print("Agent Monitor: http://localhost:8501", flush=True)
        print(
            "All services are local. Press Ctrl+C to stop. Logs: work/logs/", flush=True
        )
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError("A local service exited; inspect work/logs/")
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    main()
