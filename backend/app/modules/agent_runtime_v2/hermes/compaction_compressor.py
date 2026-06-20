"""OQIM context-compaction compressor: a kind-aware ContextCompressor subclass.

Hermes's ``ContextCompressor._generate_summary`` uses a hardcoded coding-agent
summary template (``## Active Task``, ``## Relevant Files``, ``READ x.py
[tool: read_file]``) for every agent kind. This subclass picks a kind-scoped
managed template for registered kinds (seller / personal) and delegates to the
parent (Hermes default) for everything else and for any LLM error.

For a registered kind it reproduces only the prompt *assembly* and reuses every
parent helper (``_compute_summary_budget``, ``_serialize_for_summary``,
``call_llm``, ``redact_sensitive_text``, ``self._previous_summary`` for
iterative updates, ``_with_summary_prefix``). On ANY ``call_llm`` exception it
falls back to ``super()._generate_summary(...)`` -- Hermes's full
retry/cooldown/fallback ladder. Because that ladder re-enters via
``self._generate_summary`` on its main-model retry, a registered kind keeps its
template even through the fallback retry; the coding template only ever surfaces
on the rare path where the base summarizer's immediate retry succeeds where ours
just failed. The bounce is bounded (the base sets ``_summary_model_fallen_back``
and stops) and benign.

See docs/superpowers/specs/2026-06-12-per-agent-kind-compaction-templates-design.md.
"""
from __future__ import annotations

import time
from typing import Any

from app.modules.agent_runtime_v2.hermes.compaction_templates import (
    resolve_compaction_template,
)
from app.modules.agent_runtime_v2.hermes.tool_context import current_tool_context

# Mirror of Hermes's plain, non-coding summarizer preamble (context_compressor.py
# :825-:837). Kept as our own copy because the parent's is a method-local var;
# preserves the no-greeting / same-language / redact-secrets instructions.
_COMPACTION_PREAMBLE = (
    "You are a summarization agent creating a context checkpoint. "
    "Treat the conversation turns below as source material for a "
    "compact record of prior work. "
    "Produce only the structured summary; do not add a greeting, "
    "preamble, or prefix. "
    "Write the summary in the same language the user was using in the "
    "conversation; do not translate or switch to English. "
    "NEVER include API keys, tokens, passwords, secrets, credentials, "
    "or connection strings in the summary; replace any that appear "
    "with [REDACTED]. Note that the user had credentials present, but "
    "do not preserve their values."
)

_oqim_compressor_cls: type | None = None


def _current_agent_kind() -> str | None:
    """Read agent_kind from the active ToolContext (None when absent)."""
    ctx = current_tool_context.get()
    return ctx.agent_kind if ctx is not None else None


def get_oqim_context_compressor_class() -> type:
    """Build (once) and return the OqimContextCompressor subclass.

    Lazy + memoized: the base ``ContextCompressor`` is only importable after the
    Hermes runtime is ensured, so the class is defined on first use (after
    ``apply_vendor_patches`` -> ``ensure_hermes_runtime``)."""
    global _oqim_compressor_cls
    if _oqim_compressor_cls is not None:
        return _oqim_compressor_cls

    from agent.context_compressor import ContextCompressor

    class OqimContextCompressor(ContextCompressor):
        def _generate_summary(
            self,
            turns_to_summarize: list[dict[str, Any]],
            focus_topic: str | None = None,
        ) -> str | None:
            template = resolve_compaction_template(_current_agent_kind())
            if template is None:
                return super()._generate_summary(
                    turns_to_summarize, focus_topic=focus_topic
                )
            return self._generate_kind_summary(
                turns_to_summarize, template, focus_topic=focus_topic
            )

        def _generate_kind_summary(
            self,
            turns_to_summarize: list[dict[str, Any]],
            template_sections: str,
            focus_topic: str | None = None,
        ) -> str | None:
            # Resolve call_llm / redact_sensitive_text via the module at CALL
            # time (late binding), exactly as the parent does, so they honor test
            # monkeypatches and any engine-level swap.
            import agent.context_compressor as _cc

            now = time.monotonic()
            if now < self._summary_failure_cooldown_until:
                return None

            summary_budget = self._compute_summary_budget(turns_to_summarize)
            content_to_summarize = self._serialize_for_summary(turns_to_summarize)

            template = (
                f"{template_sections.rstrip()}\n\n"
                f"Target ~{summary_budget} tokens. Be CONCRETE: keep names, "
                f"phone numbers, prices, dates, and exact values. Avoid vague "
                f"descriptions.\n\n"
                f"Write only the summary body. Do not include any preamble or prefix."
            )

            if self._previous_summary:
                prompt = (
                    f"{_COMPACTION_PREAMBLE}\n\n"
                    "You are updating a context compaction summary. A previous "
                    "compaction produced the summary below. New conversation "
                    "turns have occurred since then and need to be "
                    "incorporated.\n\n"
                    f"PREVIOUS SUMMARY:\n{self._previous_summary}\n\n"
                    f"NEW TURNS TO INCORPORATE:\n{content_to_summarize}\n\n"
                    "Update the summary using this exact structure. PRESERVE all "
                    "existing information that is still relevant. ADD new facts. "
                    "Remove information only if it is clearly obsolete.\n\n"
                    f"{template}"
                )
            else:
                prompt = (
                    f"{_COMPACTION_PREAMBLE}\n\n"
                    "Create a structured checkpoint summary for the conversation "
                    "after earlier turns are compacted. The summary should "
                    "preserve enough detail for continuity without re-reading the "
                    "original turns.\n\n"
                    f"TURNS TO SUMMARIZE:\n{content_to_summarize}\n\n"
                    f"Use this exact structure:\n\n{template}"
                )

            if focus_topic:
                prompt += (
                    f'\n\nFOCUS TOPIC: "{focus_topic}"\n'
                    "Prioritise preserving all information related to the focus "
                    "topic above. Summarise unrelated content more aggressively. "
                    "Never preserve API keys, tokens, passwords, or credentials; "
                    "use [REDACTED]."
                )

            try:
                call_kwargs: dict[str, Any] = {
                    "task": "compression",
                    "main_runtime": {
                        "model": self.model,
                        "provider": self.provider,
                        "base_url": self.base_url,
                        "api_key": self.api_key,
                        "api_mode": self.api_mode,
                    },
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": int(summary_budget * 1.3),
                }
                if self.summary_model:
                    call_kwargs["model"] = self.summary_model
                response = _cc.call_llm(**call_kwargs)
                content = response.choices[0].message.content
                if not isinstance(content, str):
                    content = str(content) if content else ""
                summary = _cc.redact_sensitive_text(content.strip())
                self._previous_summary = summary
                self._summary_failure_cooldown_until = 0.0
                self._summary_model_fallen_back = False
                self._last_summary_error = None
                return self._with_summary_prefix(summary)
            except Exception:
                # Any failure: fall back to Hermes's robust default path (its own
                # retry / fallback-to-main / cooldown ladder). That ladder
                # re-enters self._generate_summary on its main-model retry, so a
                # registered kind keeps its template through the retry.
                return super()._generate_summary(
                    turns_to_summarize, focus_topic=focus_topic
                )

    _oqim_compressor_cls = OqimContextCompressor
    return _oqim_compressor_cls
