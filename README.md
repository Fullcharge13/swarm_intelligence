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

---

## 🏗️ Architecture

```
User goal
    │
    ▼
Orchestrator.run()
    │  seeds TaskGraph with root Task(role="decomposer")
    ▼
⟳ Dispatch loop
    │  picks ready tasks → assigns idle SwarmAgent
    ▼
SwarmAgent.assign(task)
    │
    ├─ 🔍 decomposer  →  Claude returns JSON subtask list
    │                     → register_subtasks() → back to loop
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
│   ├── agent.py         # SwarmAgent — role-based execution logic
│   ├── orchestrator.py  # Async dispatch loop + agent pool
│   ├── llm.py           # Anthropic SDK wrapper (caching, batching, tiers)
│   ├── environment.py   # Blackboard — shared in-process key/value store
│   └── messaging.py     # MessageBus — async pub/sub between agents
├── tests/               # pytest test suite (35 tests, no API calls needed)
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

---

## 🐛 Bug Fixes Applied

Six confirmed bugs were identified and fixed before the initial release:

| # | Location | Issue | Fix |
|---|----------|-------|-----|
| 1 | `agent.py` | `llm.ask()` blocked the entire event loop | Wrapped with `asyncio.to_thread` |
| 2 | `orchestrator.py` | Failed task caused infinite busy-wait loop | Added `propagate_failures()` cascade |
| 3 | `task.py` | `TaskPriority(3)` raised `ValueError` crash | Changed `priority` to plain `int` |
| 4 | `task.py` | Dropped deps silently treated as satisfied | Missing deps now fail the task immediately |
| 5 | `orchestrator.py` | Agents returned to wrong model pool | `SwarmAgent` stores `complexity`; pool key corrected |
| 6 | `orchestrator.py` | Second Orchestrator got stale system prompt | System prompt always overwritten on first use |

---

## 🧪 Running Tests

```bash
pytest                          # all 35 tests
pytest tests/test_task.py       # task graph only
pytest tests/test_agent.py      # agent logic only
pytest -v                       # verbose output
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

---

## 🏗️ 아키텍처

```
사용자 목표
    │
    ▼
Orchestrator.run()
    │  TaskGraph에 root Task(role="decomposer") 추가
    ▼
⟳ 디스패치 루프
    │  준비된 태스크 선택 → 유휴 SwarmAgent에 할당
    ▼
SwarmAgent.assign(task)
    │
    ├─ 🔍 decomposer  →  Claude가 JSON 하위 태스크 목록 반환
    │                     → register_subtasks() → 루프로 복귀
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
│   ├── agent.py         # SwarmAgent — 역할 기반 실행 로직
│   ├── orchestrator.py  # 비동기 디스패치 루프 + 에이전트 풀
│   ├── llm.py           # Anthropic SDK 래퍼 (캐싱, 배치, 티어)
│   ├── environment.py   # Blackboard — 프로세스 내 공유 저장소
│   └── messaging.py     # MessageBus — 에이전트 간 비동기 pub/sub
├── tests/               # pytest 테스트 (35개, API 키 불필요)
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

---

## 🐛 수정된 버그 목록

최초 릴리스 전 6개의 확정 버그를 발견하고 수정했습니다:

| # | 위치 | 문제 | 수정 |
|---|------|------|------|
| 1 | `agent.py` | `llm.ask()`가 전체 이벤트 루프를 블로킹 | `asyncio.to_thread`로 래핑 |
| 2 | `orchestrator.py` | 태스크 실패 시 무한 busy-wait 루프 발생 | `propagate_failures()` 실패 전파 로직 추가 |
| 3 | `task.py` | `TaskPriority(3)` → `ValueError` 크래시 | `priority` 필드를 plain `int`로 변경 |
| 4 | `task.py` | max_depth로 드롭된 의존성을 충족된 것으로 오인 | 누락된 의존성이 있으면 즉시 FAILED 처리 |
| 5 | `orchestrator.py` | 에이전트가 잘못된 모델 풀로 귀환 | `SwarmAgent`에 `complexity` 저장, 풀 키 수정 |
| 6 | `orchestrator.py` | 두 번째 Orchestrator 생성 시 시스템 프롬프트 오염 | 시스템 프롬프트 항상 덮어쓰도록 수정 |

---

## 🧪 테스트 실행

```bash
pytest                          # 전체 35개 테스트
pytest tests/test_task.py       # 태스크 그래프만
pytest tests/test_agent.py      # 에이전트 로직만
pytest -v                       # 상세 출력
```

---

## 📄 요구 사항

- Python ≥ 3.11
- `anthropic >= 0.40.0`
- `pydantic >= 2.7`
- `rich >= 13.0`
