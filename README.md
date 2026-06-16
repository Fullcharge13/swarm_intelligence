# 🐝 Swarm Intelligence

> **Language / 언어** — [English](#-swarm-intelligence) · [한국어](#-swarm-intelligence-한국어)

---

## 🤖 What Is This?

**Swarm Intelligence** is a multi-agent AI system powered by [Claude](https://anthropic.com).  
A swarm of specialized agents collaboratively **decomposes**, **executes**, **reviews**, and **synthesizes** any goal you give it — automatically routing each subtask to the most cost-effective Claude model tier.

---

## ✨ Features

| Feature | Detail |
|---------|--------|
| 🧩 **Automatic decomposition** | A decomposer agent breaks your goal into a DAG of subtasks |
| ⚡ **True async concurrency** | All agents run in parallel via `asyncio` |
| 🎯 **Tiered model routing** | `simple → Haiku` · `medium → Sonnet` · `complex → Opus` |
| 💰 **Prompt caching** | System prompts cached once per role — dramatically cuts token cost |
| 📦 **Batch API support** | Independent leaf tasks submitted in one Batch API call (50% cheaper) |
| 🔁 **Agent pool reuse** | Idle agents are returned to a typed pool and reused across tasks |
| 📊 **Token usage report** | End-of-run table shows input/output/cache tokens per role tier |
| 🐜 **Pheromone trails** | Scout agents deposit weighted, time-decaying hints that guide decomposers |
| 🔍 **Scout role** | Lightweight Haiku-powered probe that predicts subtasks before execution |
| 🪝 **PostToolUse hook** | Auto-scouts on every Bash call when `SWARM_SESSION_GOAL` is set |
| ⚗️ **`/swarm` skill** | On-demand Claude Code skill: scout → orchestrate → show trail summary |

---

## 🏗️ Architecture

```
User goal
    │
    ▼
[Optional] Scout pass
    │  SwarmAgent(role="scout") predicts likely subtasks
    │  deposits trails → PheromoneBoard (.swarm/pheromones.json)
    ▼
Orchestrator.run()
    │  seeds TaskGraph with root Task(role="decomposer")
    │  injects strongest pheromone trails as Blackboard notes
    ▼
⟳ Dispatch loop
    │  picks ready tasks → assigns idle SwarmAgent
    ▼
SwarmAgent.assign(task)
    │
    ├─ 🐜 scout       →  Claude predicts subtasks (JSON) → deposits to PheromoneBoard
    ├─ 🔍 decomposer  →  Claude returns JSON subtask list (reads trail hints)
    │                     → register_subtasks() → back to loop
    │                     → reinforces/suppresses pheromone trails on DONE/FAILED
    │
    ├─ ⚙️  executor    →  Claude executes the task
    ├─ 🔎 reviewer    →  Claude reviews & annotates a result
    └─ 🔗 synthesizer →  Claude merges all results → final output
```

---

## 📁 Project Structure

```
swarm_intelligence/
├── swarm/
│   ├── task.py          # Task model + TaskGraph DAG scheduler
│   ├── agent.py         # SwarmAgent — role-based execution logic (incl. scout)
│   ├── orchestrator.py  # Async dispatch loop + agent pool + trail injection
│   ├── llm.py           # Anthropic SDK wrapper (caching, batching, tiers)
│   ├── environment.py   # Blackboard — shared in-process key/value store
│   ├── messaging.py     # MessageBus — async pub/sub between agents
│   ├── pheromone.py     # PheromoneBoard — weighted, time-decaying trail store
│   └── hooks/
│       └── scout_hook.py  # PostToolUse hook: auto-scout on Bash calls
├── .claude/
│   ├── settings.json    # PostToolUse hook registration
│   └── skills/
│       └── swarm.md     # /swarm Claude Code skill
├── tests/               # pytest test suite (71 tests, no API calls needed)
├── examples/
│   └── decompose_and_run.py
├── pyproject.toml
└── .env.example
```

---

## 🚀 Quick Start

### 1️⃣ Clone & install

```bash
git clone https://github.com/Fullcharge13/swarm_intelligence.git
cd swarm_intelligence
pip install -e ".[dev]"
```

### 2️⃣ Set your API key

```bash
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=your_key_here
```

### 3️⃣ Run the example

```bash
python examples/decompose_and_run.py
```

### 4️⃣ Run tests (no API key required)

```bash
pytest
```

---

## 🛠️ Usage

### Basic usage

```python
import asyncio
from swarm import Orchestrator

async def main():
    orchestrator = Orchestrator(max_agents=4, max_depth=2)
    result = await orchestrator.run(
        goal="Write a technical blog post about swarm intelligence",
        description="Target: software engineers. Length: ~600 words.",
    )
    print(result)

asyncio.run(main())
```

### With pheromone trails (ant colony mode)

```python
import asyncio
from swarm import Orchestrator, PheromoneBoard

async def main():
    board = PheromoneBoard()
    board.load()          # load trails from .swarm/pheromones.json (if any)
    board.evaporate()     # decay stale trails before the run

    orchestrator = Orchestrator(max_agents=4, max_depth=2, pheromone_board=board)
    result = await orchestrator.run(
        goal="Write a technical blog post about swarm intelligence",
        description="Target: software engineers. Length: ~600 words.",
    )
    board.save()          # persist updated trails for the next run
    print(result)

asyncio.run(main())
```

### `/swarm` Claude Code skill

If you're using [Claude Code](https://claude.ai/code), install the `/swarm` skill and run:

```
/swarm Write a technical blog post about swarm intelligence
```

This runs a full scout → orchestrate → trail summary pipeline entirely within your Claude Code session.

### Auto-scouting via PostToolUse hook

Set `SWARM_SESSION_GOAL` in your environment and Claude Code will automatically probe your goal after every Bash tool call:

```bash
export SWARM_SESSION_GOAL="Refactor the authentication module"
# Now every Bash command triggers a background scout pass
```

Trails accumulate in `.swarm/pheromones.json` and strengthen suggestions for future runs.

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | **Required** |
| `SWARM_MODEL_SIMPLE` | `claude-haiku-4-5-20251001` | Model for simple tasks |
| `SWARM_MODEL_MEDIUM` | `claude-sonnet-4-6` | Model for medium tasks |
| `SWARM_MODEL_COMPLEX` | `claude-opus-4-7` | Model for complex tasks |
| `SWARM_MAX_AGENTS` | `8` | Max concurrent agents |
| `SWARM_MAX_DEPTH` | `4` | Max decomposition depth |
| `SWARM_USE_BATCHING` | `false` | Enable Batch API path |
| `SWARM_SESSION_GOAL` | — | Goal for the PostToolUse scout hook (enables auto-scouting) |

---

## 🐛 Bug Fixes Applied

Nine confirmed bugs were identified and fixed:

| # | Location | Issue | Fix |
|---|----------|-------|-----|
| 1 | `agent.py` | `llm.ask()` blocked the entire event loop | Wrapped with `asyncio.to_thread` |
| 2 | `orchestrator.py` | Failed task caused infinite busy-wait loop | Added `propagate_failures()` cascade |
| 3 | `task.py` | `TaskPriority(3)` raised `ValueError` crash | Changed `priority` to plain `int` |
| 4 | `task.py` | Dropped deps silently treated as satisfied | Missing deps now fail the task immediately |
| 5 | `orchestrator.py` | Agents returned to wrong model pool | `SwarmAgent` stores `complexity`; pool key corrected |
| 6 | `orchestrator.py` | Second Orchestrator got stale system prompt | System prompt always overwritten on first use |
| 7 | `llm.py` | `batch_ask()` timed out silently with partial results | Added timeout flag + `warnings.warn` after poll loop |
| 8 | `orchestrator.py` | `_maybe_complete_parent` only worked for decomposer role | Removed role guard — any role's parent now auto-completes |
| 9 | `agent.py` | Falsy dependency results (`""`, `0`, `False`) were silently dropped | Changed `if not result` to `if result is None` |

---

## 🧪 Running Tests

```bash
pytest                              # all 71 tests
pytest tests/test_task.py           # task graph only
pytest tests/test_agent.py          # agent logic only
pytest tests/test_pheromone.py      # pheromone board only
pytest tests/test_integration.py    # end-to-end scout → trail → decomposer
pytest -v                           # verbose output
```

---

## 📄 Requirements

- Python ≥ 3.11
- `anthropic >= 0.40.0`
- `pydantic >= 2.7`
- `rich >= 13.0`

---

---

# 🐝 Swarm Intelligence (한국어)

---

## 🤖 프로젝트 소개

**Swarm Intelligence**는 [Claude](https://anthropic.com) 기반의 멀티 에이전트 AI 시스템입니다.  
특화된 에이전트들이 협력하여 주어진 목표를 자동으로 **분해 → 실행 → 검토 → 종합**하며,  
각 하위 태스크는 가장 비용 효율적인 Claude 모델 티어로 자동 라우팅됩니다.

---

## ✨ 주요 기능

| 기능 | 설명 |
|------|------|
| 🧩 **자동 목표 분해** | 분해 에이전트가 목표를 DAG 형태의 하위 태스크로 자동 분할 |
| ⚡ **진정한 비동기 동시 실행** | 모든 에이전트가 `asyncio`를 통해 병렬로 실행 |
| 🎯 **계층별 모델 라우팅** | `단순 → Haiku` · `중간 → Sonnet` · `복잡 → Opus` |
| 💰 **프롬프트 캐싱** | 역할별 시스템 프롬프트를 한 번만 캐시 — 토큰 비용 대폭 절감 |
| 📦 **배치 API 지원** | 독립 리프 태스크를 하나의 Batch API 요청으로 처리 (50% 비용 절감) |
| 🔁 **에이전트 풀 재사용** | 유휴 에이전트를 풀에 반환하여 태스크 간 재사용 |
| 📊 **토큰 사용량 리포트** | 실행 종료 시 역할 티어별 입력/출력/캐시 토큰 현황 출력 |
| 🐜 **페로몬 트레일** | 스카우트 에이전트가 가중치·시간 감쇠 힌트를 적층해 분해기를 안내 |
| 🔍 **스카우트 역할** | 실행 전 하위 태스크를 예측하는 경량 Haiku 기반 탐색 에이전트 |
| 🪝 **PostToolUse 훅** | `SWARM_SESSION_GOAL` 설정 시 Bash 호출마다 자동 스카우팅 |
| ⚗️ **`/swarm` 스킬** | 온디맨드 Claude Code 스킬: 스카우트 → 실행 → 트레일 요약 |

---

## 🏗️ 아키텍처

```
사용자 목표
    │
    ▼
[선택] 스카우트 패스
    │  SwarmAgent(role="scout")가 예상 하위 태스크 예측
    │  트레일 적층 → PheromoneBoard (.swarm/pheromones.json)
    ▼
Orchestrator.run()
    │  TaskGraph에 root Task(role="decomposer") 추가
    │  페로몬 트레일 상위 N개를 Blackboard 노트로 주입
    ▼
⟳ 디스패치 루프
    │  준비된 태스크 선택 → 유휴 SwarmAgent에 할당
    ▼
SwarmAgent.assign(task)
    │
    ├─ 🐜 scout       →  Claude가 JSON 힌트 예측 → PheromoneBoard에 적층
    ├─ 🔍 decomposer  →  Claude가 JSON 하위 태스크 목록 반환 (트레일 힌트 참조)
    │                     → register_subtasks() → 루프로 복귀
    │                     → 완료/실패 시 페로몬 트레일 강화/억제
    │
    ├─ ⚙️  executor    →  Claude가 태스크 실행
    ├─ 🔎 reviewer    →  Claude가 결과 검토 및 주석 추가
    └─ 🔗 synthesizer →  Claude가 전체 결과 통합 → 최종 출력
```

---

## 📁 프로젝트 구조

```
swarm_intelligence/
├── swarm/
│   ├── task.py          # Task 모델 + TaskGraph DAG 스케줄러
│   ├── agent.py         # SwarmAgent — 역할 기반 실행 로직 (스카우트 포함)
│   ├── orchestrator.py  # 비동기 디스패치 루프 + 에이전트 풀 + 트레일 주입
│   ├── llm.py           # Anthropic SDK 래퍼 (캐싱, 배치, 티어)
│   ├── environment.py   # Blackboard — 프로세스 내 공유 저장소
│   ├── messaging.py     # MessageBus — 에이전트 간 비동기 pub/sub
│   ├── pheromone.py     # PheromoneBoard — 가중치·시간 감쇠 트레일 저장소
│   └── hooks/
│       └── scout_hook.py  # PostToolUse 훅: Bash 호출 시 자동 스카우팅
├── .claude/
│   ├── settings.json    # PostToolUse 훅 등록
│   └── skills/
│       └── swarm.md     # /swarm Claude Code 스킬
├── tests/               # pytest 테스트 (71개, API 키 불필요)
├── examples/
│   └── decompose_and_run.py
├── pyproject.toml
└── .env.example
```

---

## 🚀 빠른 시작

### 1️⃣ 클론 & 설치

```bash
git clone https://github.com/Fullcharge13/swarm_intelligence.git
cd swarm_intelligence
pip install -e ".[dev]"
```

### 2️⃣ API 키 설정

```bash
cp .env.example .env
# .env 파일을 열어 ANTHROPIC_API_KEY=your_key_here 입력
```

### 3️⃣ 예제 실행

```bash
python examples/decompose_and_run.py
```

### 4️⃣ 테스트 실행 (API 키 불필요)

```bash
pytest
```

---

## 🛠️ 사용 예시

### 기본 사용

```python
import asyncio
from swarm import Orchestrator

async def main():
    orchestrator = Orchestrator(max_agents=4, max_depth=2)
    result = await orchestrator.run(
        goal="스웜 인텔리전스에 관한 기술 블로그 포스트 작성",
        description="대상: 소프트웨어 엔지니어. 분량: 약 600단어.",
    )
    print(result)

asyncio.run(main())
```

### 페로몬 트레일 활성화 (개미 군집 모드)

```python
import asyncio
from swarm import Orchestrator, PheromoneBoard

async def main():
    board = PheromoneBoard()
    board.load()          # .swarm/pheromones.json에서 트레일 로드 (있으면)
    board.evaporate()     # 실행 전 오래된 트레일 감쇠

    orchestrator = Orchestrator(max_agents=4, max_depth=2, pheromone_board=board)
    result = await orchestrator.run(
        goal="스웜 인텔리전스에 관한 기술 블로그 포스트 작성",
        description="대상: 소프트웨어 엔지니어. 분량: 약 600단어.",
    )
    board.save()          # 다음 실행을 위해 업데이트된 트레일 저장
    print(result)

asyncio.run(main())
```

### `/swarm` Claude Code 스킬

[Claude Code](https://claude.ai/code)를 사용 중이라면 `/swarm` 스킬을 설치한 뒤:

```
/swarm 스웜 인텔리전스에 관한 기술 블로그 포스트 작성
```

스카우트 → 실행 → 트레일 요약 파이프라인이 Claude Code 세션 내에서 자동 실행됩니다.

### PostToolUse 훅으로 자동 스카우팅

환경 변수에 `SWARM_SESSION_GOAL`을 설정하면 Claude Code가 모든 Bash 도구 호출 후 자동으로 스카우팅합니다:

```bash
export SWARM_SESSION_GOAL="인증 모듈 리팩터링"
# 이제 모든 Bash 명령이 백그라운드 스카우트 패스를 트리거합니다
```

트레일은 `.swarm/pheromones.json`에 누적되며 이후 실행의 제안을 강화합니다.

---

## ⚙️ 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ANTHROPIC_API_KEY` | — | **필수** |
| `SWARM_MODEL_SIMPLE` | `claude-haiku-4-5-20251001` | 단순 태스크용 모델 |
| `SWARM_MODEL_MEDIUM` | `claude-sonnet-4-6` | 중간 태스크용 모델 |
| `SWARM_MODEL_COMPLEX` | `claude-opus-4-7` | 복잡 태스크용 모델 |
| `SWARM_MAX_AGENTS` | `8` | 최대 동시 에이전트 수 |
| `SWARM_MAX_DEPTH` | `4` | 최대 분해 깊이 |
| `SWARM_USE_BATCHING` | `false` | Batch API 경로 활성화 |
| `SWARM_SESSION_GOAL` | — | PostToolUse 스카우트 훅용 목표 (자동 스카우팅 활성화) |

---

## 🐛 수정된 버그 목록

총 9개의 확정 버그를 발견하고 수정했습니다:

| # | 위치 | 문제 | 수정 |
|---|------|------|------|
| 1 | `agent.py` | `llm.ask()`가 전체 이벤트 루프를 블로킹 | `asyncio.to_thread`로 래핑 |
| 2 | `orchestrator.py` | 태스크 실패 시 무한 busy-wait 루프 발생 | `propagate_failures()` 실패 전파 로직 추가 |
| 3 | `task.py` | `TaskPriority(3)` → `ValueError` 크래시 | `priority` 필드를 plain `int`로 변경 |
| 4 | `task.py` | max_depth로 드롭된 의존성을 충족된 것으로 오인 | 누락된 의존성이 있으면 즉시 FAILED 처리 |
| 5 | `orchestrator.py` | 에이전트가 잘못된 모델 풀로 귀환 | `SwarmAgent`에 `complexity` 저장, 풀 키 수정 |
| 6 | `orchestrator.py` | 두 번째 Orchestrator 생성 시 시스템 프롬프트 오염 | 시스템 프롬프트 항상 덮어쓰도록 수정 |
| 7 | `llm.py` | `batch_ask()` 타임아웃 시 부분 결과를 무음으로 반환 | 타임아웃 플래그 추가 및 `warnings.warn`으로 경고 출력 |
| 8 | `orchestrator.py` | `_maybe_complete_parent`가 decomposer 역할에만 동작 | 역할 조건 제거 — 모든 역할의 부모 태스크 자동 완료 처리 |
| 9 | `agent.py` | 빈 문자열/0/False 등 falsy 의존성 결과가 무음으로 스킵 | `if not result` → `if result is None`으로 변경 |

---

## 🧪 테스트 실행

```bash
pytest                              # 전체 71개 테스트
pytest tests/test_task.py           # 태스크 그래프만
pytest tests/test_agent.py          # 에이전트 로직만
pytest tests/test_pheromone.py      # 페로몬 보드만
pytest tests/test_integration.py    # 스카우트 → 트레일 → 분해기 E2E
pytest -v                           # 상세 출력
```

---

## 📄 요구 사항

- Python ≥ 3.11
- `anthropic >= 0.40.0`
- `pydantic >= 2.7`
- `rich >= 13.0`
