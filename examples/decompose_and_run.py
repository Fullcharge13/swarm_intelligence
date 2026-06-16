"""Example: run the swarm on a task decomposition goal.

Usage:
    python examples/decompose_and_run.py
"""

import asyncio

from swarm import Orchestrator


async def main() -> None:
    orchestrator = Orchestrator(max_agents=4, max_depth=2)

    result = await orchestrator.run(
        goal="Write a short technical blog post about swarm intelligence",
        description=(
            "The post should cover: what swarm intelligence is, key algorithms "
            "(ACO, PSO, boids), and real-world applications. "
            "Target audience: software engineers with no prior knowledge. "
            "Length: ~600 words."
        ),
    )

    print("\n===== FINAL RESULT =====")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
