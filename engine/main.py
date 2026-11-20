"""Local policy API. Run with uvicorn engine.main:app --host 127.0.0.1 --port 8000."""

import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from contracts.schemas import (
    BaselineObservation,
    CapabilityEvidence,
    DemoRequest,
    Event,
    ExecutionReport,
    FailRunRequest,
    ModelUsage,
    RunSummary,
    StartRunRequest,
    StartRunResponse,
    Verdict,
    Violation,
)
from engine.policy import PolicySettings
from engine.runs import RunError, RunManager
from engine.otari import OtariConfig, OtariError
from store.db import EvidenceStore
from mcp_tools.state import ToolState
from engine.capabilities import ProfileSettings
from engine.coverage import coverage


def create_app(
    settings: PolicySettings | None = None, *, db_path=":memory:", tool_state_path=None
) -> FastAPI:
    tasks = set()
    manager = RunManager(settings or PolicySettings.from_env(), EvidenceStore(db_path))

    @asynccontextmanager
    async def lifespan(app):
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        manager.store.close()

    app = FastAPI(title="Agent Monitor Policy", version="0.2.0", lifespan=lifespan)
    app.state.runs = manager
    app.state.runner = None

    def otari_ready():
        try:
            return bool(OtariConfig.from_env().model)
        except OtariError:
            return False

    @app.exception_handler(RunError)
    async def run_error(request: Request, exc: RunError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI's default error includes submitted values; omit them entirely.
        return JSONResponse(
            status_code=422,
            content={"detail": "Request does not match the shared contract"},
        )

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "evidence_storage": "sqlite",
            "durable": manager.store.durable,
            "otari_ready": otari_ready(),
        }

    @app.get("/services")
    async def services():
        async def available(url):
            parsed = urlsplit(url)
            try:
                host = (
                    "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
                )
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, parsed.port or 80), timeout=1
                )
                writer.close()
                await writer.wait_closed()
                return True
            except (OSError, TimeoutError):
                return False

        names = {
            "gateway": os.getenv("MCP_GATEWAY_URL", "http://localhost:8200/mcp"),
            "tools": os.getenv("MCP_UPSTREAM_URL", "http://localhost:8100/mcp"),
        }
        states = await asyncio.gather(*(available(url) for url in names.values()))
        return {"engine": True, **dict(zip(names, states))}

    @app.get("/runs", response_model=list[RunSummary])
    def list_runs():
        return manager.store.list_runs()

    @app.get("/otari/profile")
    def otari_profile():
        try:
            return ProfileSettings.from_env().public()
        except OtariError:
            raise HTTPException(
                503,
                "Check the distinct model roles and public campaign reference configuration",
            ) from None

    @app.post("/runs/start", response_model=StartRunResponse)
    def start_run(request: StartRunRequest):
        return StartRunResponse(
            **manager.start(request.mode).model_dump(include={"run_id", "mode"})
        )

    @app.post("/check", response_model=Verdict)
    def check(event: Event):
        return manager.check(event)

    @app.get("/runs/{run_id}", response_model=RunSummary)
    def get_run(run_id: UUID):
        return manager.summary(run_id)

    @app.get("/runs/{run_id}/violations", response_model=list[Violation])
    def violations(run_id: UUID):
        return manager.violations(run_id)

    @app.post("/runs/{run_id}/executions", response_model=ExecutionReport)
    def execution(run_id: UUID, report: ExecutionReport):
        return manager.report(run_id, report)

    @app.post("/runs/{run_id}/end", response_model=RunSummary)
    def end_run(run_id: UUID):
        return manager.end(run_id)

    @app.post("/runs/{run_id}/fail", response_model=RunSummary)
    def fail_run(run_id: UUID, request: FailRunRequest):
        return manager.end(run_id, failed=True, reason=request.reason)

    @app.post("/runs/{run_id}/observations", response_model=BaselineObservation)
    def observe(run_id: UUID, observation: BaselineObservation):
        return manager.observe_baseline(run_id, observation)

    @app.post("/runs/{run_id}/usage", response_model=ModelUsage)
    def record_usage(run_id: UUID, usage: ModelUsage):
        return manager.record_usage(run_id, usage)

    @app.get("/runs/{run_id}/usage")
    def usage(run_id: UUID):
        manager.summary(run_id)
        return manager.store.usage(run_id)

    @app.get("/runs/{run_id}/timeline")
    def timeline(run_id: UUID):
        manager.summary(run_id)
        return manager.store.timeline(run_id)

    @app.post("/runs/{run_id}/capabilities", response_model=CapabilityEvidence)
    def record_capability(run_id: UUID, evidence: CapabilityEvidence):
        return manager.record_capability(run_id, evidence)

    @app.get("/runs/{run_id}/capabilities")
    def capabilities(run_id: UUID):
        manager.summary(run_id)
        return manager.store.capabilities(run_id)

    @app.get("/runs/{run_id}/coverage")
    def rubric_coverage(run_id: UUID):
        manager.summary(run_id)
        return coverage(manager.store.usage(run_id), manager.store.capabilities(run_id))

    @app.get("/runs/{run_id}/tool-writes")
    def tool_writes(run_id: UUID):
        manager.summary(run_id)
        try:
            return ToolState.list_writes(
                tool_state_path or os.getenv("TOOL_STATE_PATH", "work/tool_state.db"),
                run_id,
            )
        except sqlite3.Error:
            raise HTTPException(503, "Synthetic tool state is unavailable") from None

    @app.post("/demo/run", response_model=StartRunResponse, status_code=202)
    async def demo_run(spec: DemoRequest, request: Request):
        if spec.capability_profile and spec.driver != "otari":
            raise HTTPException(
                422, "The Otari capability workflow requires the live Otari driver"
            )
        if spec.driver == "otari" and not otari_ready():
            raise HTTPException(
                503,
                "Configure OTARI_BASE_URL, OTARI_AGENT_KEY, and OTARI_AGENT_MODEL in .env first",
            )
        from agent.run import configured_runner

        if spec.capability_profile:
            try:
                ProfileSettings.from_env()
            except OtariError:
                raise HTTPException(
                    503, "Check distinct model roles and the campaign reference URL"
                ) from None
        run = manager.start(
            spec.mode,
            driver=spec.driver,
            scenario=spec.scenario,
            capability_profile=spec.capability_profile,
        )
        runner = app.state.runner or configured_runner(
            str(request.base_url).rstrip("/")
        )
        task = asyncio.create_task(runner.execute(run.run_id, spec))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return StartRunResponse(run_id=run.run_id, mode=run.mode)

    return app


load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
app = create_app(db_path=os.getenv("DB_PATH", "store/agent_monitor.db"))
