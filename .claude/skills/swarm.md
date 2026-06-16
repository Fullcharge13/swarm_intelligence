---
name: swarm
description: Run the swarm_intelligence pipeline on a goal. Invokes a scout pass then full decompose→execute→review→synthesize flow. Use when the user wants to delegate a complex goal to the multi-agent swarm.
---

# /swarm — Swarm Intelligence Skill

Use this skill when the user types `/swarm <goal>` to run the full ant-colony-inspired swarm pipeline.

## Steps

1. **Load and age existing pheromone trails**

   ```python
   from swarm.pheromone import PheromoneBoard
   board = PheromoneBoard()
   board.load()
   board.evaporate(decay_rate=0.1)
   ```

2. **Run scout pass** (fast, Haiku — pre-loads trail hints)

   ```python
   import asyncio
   from swarm.agent import SwarmAgent, ROLE_PROMPTS
   from swarm.environment import Blackboard
   from swarm.llm import ROLE_MAX_TOKENS, make_client
   from swarm.messaging import MessageBus
   from swarm.task import Task

   llm = make_client(role="scout", complexity="simple")
   llm.system = ROLE_PROMPTS["scout"]
   llm.max_tokens = ROLE_MAX_TOKENS.get("scout", 512)

   scout = SwarmAgent(
       role="scout", complexity="simple",
       blackboard=Blackboard(), bus=MessageBus(),
       llm=llm, pheromone_board=board,
   )
   scout_task = Task(title=goal, description=goal, required_role="scout", complexity="simple")
   asyncio.run(scout.assign(scout_task))
   ```

3. **Run full swarm pipeline**

   ```python
   import asyncio
   from swarm import Orchestrator

   orchestrator = Orchestrator(pheromone_board=board)
   result = asyncio.run(orchestrator.run(goal=goal))
   print(result)
   ```

4. **Save updated trails**

   ```python
   board.save()
   ```

5. **Show trail summary** — report to the user:
   - The final synthesised result
   - Top 5 pheromone trails and their weights (call `board.strongest(n=5)`)
   - Any trails that reinforced or evaporated during the run

## Setting the session goal for background hooks

To enable passive background scouting (via the PostToolUse hook), set the
`SWARM_SESSION_GOAL` environment variable before starting Claude Code:

```bash
export SWARM_SESSION_GOAL="my project goal here"
```

The hook will then run scouts automatically after each Bash command.
