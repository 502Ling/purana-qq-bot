"""诊断脚本：测学校网关（ECUST）的模型连通性。

用法（在项目根目录跑）：

    .\\venv\\Scripts\\python.exe check_models.py                 # 只测 .env 里的 ECUST_MODEL
    .\\venv\\Scripts\\python.exe check_models.py glm-5.3         # 测指定模型（可写多个）
    .\\venv\\Scripts\\python.exe check_models.py --all           # 测所有对话模型（跳过 embedding/reranker）
    .\\venv\\Scripts\\python.exe check_models.py --all --timeout 20

退出码：全部通过 = 0，有失败 = 1。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

import aiohttp

from bot.config import load_settings
from bot.ecust import EcustClient

PROMPT = "回复一个字：好"
SKIP_KEYWORDS = ("embed", "rerank", "bge", "gte")


def _chat_models(models: list[str]) -> list[str]:
    return [m for m in models if not any(k in m.lower() for k in SKIP_KEYWORDS)]


async def _probe(
    client: EcustClient,
    session: aiohttp.ClientSession,
    model: str,
    sem: asyncio.Semaphore,
) -> tuple[str, float, str]:
    async with sem:
        t0 = time.perf_counter()
        reply, ok = await client.ask(session, PROMPT, model)
        dt = time.perf_counter() - t0
    if ok:
        head = " ".join(reply.split())[:24] or "(空回复)"
        return model, dt, f"OK   {dt:6.1f}s  {head}"
    return model, dt, f"FAIL {dt:6.1f}s  {reply}"


async def main() -> int:
    ap = argparse.ArgumentParser(description="测 ECUST 网关模型连通性")
    ap.add_argument("models", nargs="*", help="要测的模型 id（省略 = .env 的默认模型）")
    ap.add_argument("--all", action="store_true", help="测所有对话模型")
    ap.add_argument("--timeout", type=float, default=30.0, help="单次超时秒数（默认 30）")
    ap.add_argument("--concurrency", type=int, default=4, help="并发数（默认 4）")
    args = ap.parse_args()

    s = load_settings()
    if not s.ecust_base_url or not s.ecust_api_key:
        print("ECUST_BASE_URL / ECUST_API_KEY 没配，检查 .env")
        return 2

    client = EcustClient(s.ecust_base_url, s.ecust_api_key, s.ecust_model, args.timeout)

    async with aiohttp.ClientSession() as session:
        if args.all:
            targets = _chat_models(await client.models(session))
            if not targets:
                print("拉取模型列表失败（/models 不通？）")
                return 2
        elif args.models:
            targets = args.models
        else:
            targets = [s.ecust_model]

        print(f"网关 {s.ecust_base_url}  |  测 {len(targets)} 个模型  |  超时 {args.timeout:g}s")
        print("-" * 60)

        sem = asyncio.Semaphore(max(1, args.concurrency))
        results = await asyncio.gather(*(_probe(client, session, m, sem) for m in targets))

    failed = 0
    for model, _dt, line in results:
        print(f"{line:<50}  {model}")
        if line.startswith("FAIL"):
            failed += 1

    print("-" * 60)
    print(f"通过 {len(results) - failed} / {len(results)}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
