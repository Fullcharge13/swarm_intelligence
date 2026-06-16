"""Claude API wrapper — token-optimised.

Four optimisations live here:

1. **Shared clients** — ``make_client(role, complexity)`` returns a *cached*
   singleton keyed by ``(role, complexity)``.  All agents of the same role+tier
   share one client → system prompt is cached once per tier, not per agent.

2. **Tiered model routing** — three model tiers driven by task complexity:
   ``simple → Haiku``, ``medium → Sonnet``, ``complex → Opus``.
   The decomposer always gets Opus; the internal summariser always gets Haiku.

3. **Role-based max_tokens** — each role has a sensible output cap, preventing
   runaway generation (especially the decomposer which only needs compact JSON).

4. **Batch API** — ``batch_ask()`` submits multiple requests in a single
   Batch API call (50 % cheaper).  Used by the orchestrator for independent
   leaf tasks when ``SWARM_USE_BATCHING=true``.

5. **Context summarisation helper** — ``quick_summarize()`` condenses a long
   dependency result using the shared Haiku client before it is injected into
   downstream agent prompts.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Model tiers  (all overridable via env vars)
# ---------------------------------------------------------------------------

COMPLEXITY_MODELS: dict[str, str] = {
    "simple":  os.getenv("SWARM_MODEL_SIMPLE",  "claude-haiku-4-5-20251001"),
    "medium":  os.getenv("SWARM_MODEL_MEDIUM",  "claude-sonnet-4-6"),
    "complex": os.getenv("SWARM_MODEL_COMPLEX", "claude-opus-4-7"),
}

# Role → default complexity tier (can be overridden at the task level)
ROLE_COMPLEXITY: dict[str, str] = {
    "decomposer":  "complex",  # planning benefits from Opus reasoning
    "executor":    "medium",   # overridden per-task by complexity field
    "reviewer":    "medium",
    "synthesizer": "complex",  # combining many results needs full context
    "_summarizer": "simple",   # Haiku is sufficient for compaction
    "scout":       "simple",   # probing only — Haiku is sufficient
}

# Role → maximum output tokens
ROLE_MAX_TOKENS: dict[str, int] = {
    "decomposer":  1_024,   # JSON schema only — no long prose needed
    "executor":    2_048,
    "reviewer":    2_048,
    "synthesizer": 6_144,
    "_summarizer":   256,   # tight cap: one short paragraph
    "scout":         512,   # JSON hints only
}


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------

class LLMClient:
    """Thin wrapper around the Anthropic SDK with caching and usage tracking.

    Parameters
    ----------
    model:      Model ID string.
    system:     System prompt — automatically marked for prompt caching.
    max_tokens: Hard cap on response length.
    """

    def __init__(
        self,
        model: str = COMPLEXITY_MODELS["medium"],
        system: str = "",
        max_tokens: int = 2_048,
    ) -> None:
        self.model = model
        self.system = system
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        # Cumulative counters — read-only externally
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.cache_read_tokens: int = 0
        self.cache_write_tokens: int = 0  # NEW: cache creation cost

    # ------------------------------------------------------------------
    # Real-time call
    # ------------------------------------------------------------------

    def chat(self, messages: list[dict[str, Any]]) -> str:
        """Send *messages* and return the assistant text."""
        system_block: list[dict[str, Any]] = (
            [{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}]
            if self.system
            else []
        )

        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_block or anthropic.NOT_GIVEN,
            messages=messages,
        )

        usage = response.usage
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        if hasattr(usage, "cache_read_input_tokens"):
            self.cache_read_tokens += usage.cache_read_input_tokens  # type: ignore[attr-defined]
        if hasattr(usage, "cache_creation_input_tokens"):
            self.cache_write_tokens += usage.cache_creation_input_tokens  # type: ignore[attr-defined]

        return response.content[0].text  # type: ignore[index]

    def ask(self, prompt: str) -> str:
        """Single-turn convenience wrapper."""
        return self.chat([{"role": "user", "content": prompt}])

    # ------------------------------------------------------------------
    # Batch API (Opt. 4 — 50 % cheaper for independent leaf tasks)
    # ------------------------------------------------------------------

    async def batch_ask(
        self,
        requests: list[dict[str, Any]],
        poll_interval: float = 5.0,
        max_wait: float = 3_600.0,
    ) -> dict[str, str]:
        """Submit *requests* via the Message Batches API and return results.

        Parameters
        ----------
        requests:
            Each entry must have ``"custom_id"`` (str) and ``"params"``
            (a dict matching the ``messages.create`` signature).
        poll_interval:
            Seconds between status polls.
        max_wait:
            Give up after this many seconds and return partial results.

        Returns
        -------
        dict mapping ``custom_id → result_text`` for succeeded requests.
        """
        batch = self._client.messages.batches.create(requests=requests)
        elapsed = 0.0
        timed_out = True

        while elapsed < max_wait:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            status = self._client.messages.batches.retrieve(batch.id)
            if status.processing_status == "ended":
                timed_out = False
                break

        if timed_out:
            import warnings
            warnings.warn(
                f"batch_ask timed out after {max_wait}s for batch {batch.id}; "
                "returning partial results",
                RuntimeWarning,
                stacklevel=2,
            )

        results: dict[str, str] = {}
        for item in self._client.messages.batches.results(batch.id):  # type: ignore[attr-defined]
            if item.result.type == "succeeded":
                results[item.custom_id] = item.result.message.content[0].text
                u = item.result.message.usage
                self.total_input_tokens += u.input_tokens
                self.total_output_tokens += u.output_tokens

        return results

    # ------------------------------------------------------------------
    # Usage reporting
    # ------------------------------------------------------------------

    def usage_summary(self) -> dict[str, int]:
        return {
            "input_tokens":       self.total_input_tokens,
            "output_tokens":      self.total_output_tokens,
            "cache_read_tokens":  self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }

    def cache_hit_rate(self) -> float:
        """Fraction of input tokens served from cache (0–1)."""
        denom = self.total_input_tokens + self.cache_read_tokens
        return self.cache_read_tokens / denom if denom else 0.0


# ---------------------------------------------------------------------------
# Opt. 1 — Shared client registry  (one instance per role+complexity pair)
# ---------------------------------------------------------------------------

_registry: dict[tuple[str, str], LLMClient] = {}


def make_client(role: str = "executor", complexity: str | None = None) -> LLMClient:
    """Return a *shared* ``LLMClient`` for the given role/complexity pair.

    Sharing means the system prompt is cached once across all agents of the
    same role, dramatically increasing the cache hit rate.

    The ``system`` prompt must be set *after* calling this function (the
    orchestrator injects it when the first agent of each role is created).
    """
    resolved = complexity or ROLE_COMPLEXITY.get(role, "medium")
    key = (role, resolved)
    if key not in _registry:
        _registry[key] = LLMClient(
            model=COMPLEXITY_MODELS[resolved],
            max_tokens=ROLE_MAX_TOKENS.get(role, 2_048),
        )
    return _registry[key]


def reset_registry() -> None:
    """Clear the shared client registry.  Call between test runs."""
    _registry.clear()


# ---------------------------------------------------------------------------
# Opt. 2 — Context summarisation helper
# ---------------------------------------------------------------------------

_SUMMARIZER_SYSTEM = (
    "You are a concise summariser. "
    "Given a piece of text, write a ≤150-word summary that preserves "
    "all key facts, conclusions, and action items. "
    "Output only the summary — no preamble."
)


def quick_summarize(text: str) -> str:
    """Condense *text* to ≤150 words using the shared Haiku client.

    Used by agents to compress large dependency results before injecting
    them into downstream prompts.
    """
    client = make_client("_summarizer")
    if not client.system:
        client.system = _SUMMARIZER_SYSTEM
    return client.ask(f"Summarise the following:\n\n{text}")
