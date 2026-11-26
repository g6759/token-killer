"""Run the sidebar's subscription client against the desktop game's MCP tools."""
import asyncio
import json

from .server import DEFAULT_PORT, make_server
from mcp.shared.memory import create_connected_server_and_client_session


async def play(jobs, pack, key, driver):
    model_response = None
    arguments = {"pack_id": pack, "job_id": key}

    def dispatch(pack_id, path, payload):
        if pack_id != pack or payload.get("id") != key:
            raise ValueError("当前执行器只能操作分配给它的任务。")
        if path == "/api/auto/claim":
            return jobs.claim(pack, key, worker=True)
        if path == "/api/auto/submit" and model_response is not None:
            return jobs.submit(pack, key, payload["turn"], payload["candidate"], model_response=model_response)
        raise ValueError("订阅执行器只可领取和提交本局轮次。")

    def cancelled():
        with jobs.transaction(False) as db:
            return jobs._lookup(db, pack, key)["status"] != "running"

    error = None
    server = make_server(DEFAULT_PORT, dispatch=dispatch)
    async with create_connected_server_and_client_session(server) as client:
        async def call(name, **extra):
            response = await client.call_tool(name, {**arguments, **extra})
            if response.isError:
                raise ValueError(response.content[0].text)
            return response.structuredContent

        try:
            while not cancelled():
                task = await call("claim_turn")
                turn = task["turn"]
                if turn is None:
                    break
                prompt = task["prompt"]
                if task["previous_receipt"]:
                    prompt = ("上一轮裁判回执：" + task["previous_receipt"]["message"] +
                              "\n请结合回执尝试不同的改动。\n" + prompt)

                def on_ready():
                    jobs.worker_stage(pack, key, turn, f"模型正在推理 · 第 {turn} 轮")

                result = await asyncio.to_thread(driver.infer, prompt, cancelled=cancelled, on_ready=on_ready)
                if cancelled():
                    break
                jobs.worker_stage(pack, key, turn, "正在交给裁判复核")
                raw = result["text"]
                try:
                    candidate = json.loads(raw)
                except (ValueError, TypeError):
                    candidate = None
                model_response = {"model": result.get("model"), "usage": result.get("usage")}
                if not isinstance(candidate, dict):
                    candidate = {}
                    model_response["invalid_output"] = str(raw)[:4096]
                receipt = await call("submit_turn", turn=turn, candidate=candidate)
                if receipt["turn"] is None:
                    break
        except Exception as exc:
            # Preserve the original preflight error when leaving the SDK's task group.
            error = exc
    if error is not None:
        raise error
