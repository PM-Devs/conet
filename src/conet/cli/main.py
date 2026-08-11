import argparse
import asyncio
import os

import grpc

from conet.persistence.store import Store
from conet.protocols.grpc import skillruntime_pb2 as pb2
from conet.protocols.grpc import skillruntime_pb2_grpc as pb2_grpc


def _db_path() -> str:
    return os.environ.get('CONET_DB_PATH', 'conet.db')


def _channel_target(endpoint: str) -> str:
    return endpoint.split('://', 1)[-1]  # "grpc://host:port" -> "host:port"


async def _status() -> None:
    store = Store(_db_path())
    try:
        agents = await store.list_all_agents()
    except Exception as exc:  # noqa: BLE001 — a CLI status check reports any failure to the operator, it doesn't crash for one
        print(f'CoNET control plane: UNREACHABLE ({exc})')
        return
    finally:
        await store.close()
    print('CoNET control plane: healthy')
    print(f'Active agents: {len(agents)}')


async def _agents() -> None:
    store = Store(_db_path())
    try:
        agents = await store.list_all_agents()
    finally:
        await store.close()
    if not agents:
        print('No agents registered')
        return
    for agent in agents:
        print(f'{agent.name}\tdept={agent.department}\tframework={agent.framework}\tendpoint={agent.endpoint}')


async def _skills() -> None:
    store = Store(_db_path())
    try:
        agents = await store.list_all_agents()
    finally:
        await store.close()
    rows = [(agent.name, skill.skill_id, skill.version) for agent in agents for skill in agent.skills]
    if not rows:
        print('No skills published')
        return
    for agent_name, skill_id, version in rows:
        print(f'{skill_id}\tv{version}\tprovider={agent_name}')


async def _cancel(task_id: str) -> None:
    store = Store(_db_path())
    try:
        task = await store.get_task(task_id)
        if task is None:
            print(f'No such task: {task_id}')
            return
        if task.provider is None:
            print(f'Task {task_id} has no known provider')
            return
        provider = await store.get_agent(task.provider)
    finally:
        await store.close()

    if provider is None:
        print(f"Task {task_id}'s provider '{task.provider}' is no longer registered")
        return

    channel = grpc.aio.insecure_channel(_channel_target(provider.endpoint))
    try:
        stub = pb2_grpc.SkillRuntimeStub(channel)
        ack = await stub.Cancel(pb2.CancelRequest(task_id=task_id))
    finally:
        await channel.close()

    if ack.acknowledged:
        print(f'Task {task_id} cancelled')
    else:
        print(f'Task {task_id} was not running (nothing to cancel)')


def main() -> None:
    parser = argparse.ArgumentParser(prog='conet', description='CoNET operator CLI')
    subparsers = parser.add_subparsers(dest='command')
    subparsers.add_parser('status', help='Show control plane status')
    subparsers.add_parser('agents', help='List registered agents')
    subparsers.add_parser('skills', help='List published skills')
    cancel_parser = subparsers.add_parser('cancel', help='Cancel a running task')
    cancel_parser.add_argument('task_id')

    args = parser.parse_args()

    if args.command == 'status':
        asyncio.run(_status())
    elif args.command == 'agents':
        asyncio.run(_agents())
    elif args.command == 'skills':
        asyncio.run(_skills())
    elif args.command == 'cancel':
        asyncio.run(_cancel(args.task_id))
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
