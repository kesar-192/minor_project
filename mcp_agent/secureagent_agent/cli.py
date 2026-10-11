"""Talk to the agent:  python -m secureagent_agent  [--ask "Send 0.05 ETH to Alice"]

The agent starts the MCP server itself. Set the planner in .env (AGENT_PLANNER) or with --planner.
"""

import argparse
import asyncio
import logging
import sys

from mcp import Client

from secureagent_mcp.config import ConfigError, load_env

from .config import AgentSettings
from .loop import AgentSession
from .messages import PlannerError
from .planners import make_planner
from .runtime import START_FAILED, is_start_failure, mcp_params

RED, GREEN, DIM, RESET = "\033[31m", "\033[32m", "\033[2m", "\033[0m"


def describe_calls(result):
    parts = []
    for call in result.calls:
        reply = call.reply
        if reply.get("decision") == "ALLOW":
            parts.append(f"{GREEN}{call.tool} ALLOW{RESET}")
        else:
            where = f" gate {reply['gate_number']}" if reply.get("gate_number") is not None else ""
            parts.append(f"{RED}{call.tool} DENY{where} {reply.get('reason_code')}{RESET}")
    return " -> ".join(parts)


async def run(args):
    # The agent process does not get the Vault session token: only the MCP server reads it.
    env = {k: v for k, v in load_env(args.env_file).items() if k != "AGENT_TOKEN"}
    settings = AgentSettings.from_env({**env, **({"AGENT_PLANNER": args.planner} if args.planner else {})})
    planner = make_planner(settings)
    params = mcp_params(args.vault_url, attack_demo=args.attack_demo, env_file=args.env_file)
    async with Client(params) as client:
        session = AgentSession(planner, client, settings.max_steps, settings.max_lookups)
        print(f"{DIM}planner: {planner.name}. Type a request, or 'quit'.{RESET}")
        questions = [args.ask] if args.ask else None
        while True:
            if questions is not None:
                if not questions:
                    return
                text = questions.pop(0)
                print(f"> {text}")
            else:
                try:
                    text = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return
            if text.lower() in {"quit", "exit"}:
                return
            if not text:
                continue
            result = await session.handle(text)
            print(result.text)
            print(f"{DIM}trace {result.trace_id}{RESET}  {describe_calls(result)}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="secureagent_agent", description=__doc__.split("\n")[0])
    parser.add_argument("--planner", choices=["scripted", "openrouter", "anthropic", "openai"])
    parser.add_argument("--ask", help="ask one thing and exit")
    parser.add_argument("--vault-url", help="override VAULT_URL for the MCP server")
    parser.add_argument("--env-file")
    parser.add_argument("--attack-demo", action="store_true", help="list export_private_key for the attack demo")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        asyncio.run(run(args))
    except (ConfigError, PlannerError) as exc:
        sys.exit(f"secureagent_agent: {exc}")
    except BaseException as exc:
        if is_start_failure(exc):
            sys.exit(f"secureagent_agent: {START_FAILED}")
        raise


if __name__ == "__main__":
    main()
