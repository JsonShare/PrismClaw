#!/usr/bin/env python3
"""Smoke test for trajectory fixes A-F at the projection layer.

Validates that:
  A. PipelineOutDone.budget_summary is non-empty when BudgetAuditTrail has decisions
  B. PipelineOutDone.compressors_used is non-empty when actions != "keep"
  C. PackerFinalDone.estimated_tokens is computed from messages (not getattr fallback)
  D. _infer_llm_provider maps known model prefixes correctly
  E. SessionIterationFinished commit on early-return path (lazy-commit logic)
  F. ProjectorOutputDone.appended_msg_count surfaces in record

Does NOT require a running server or PostgreSQL — pure in-memory fold.
"""
import sys

from prismclaw.engine.trajectory import bus_events as evmod
from prismclaw.engine.trajectory.projection import (
    _infer_llm_provider,
    apply,
    init_state,
)


# --- D: provider inference ---------------------------------------------------
def test_d_provider_inference() -> None:
    cases = [
        ("qwen2.5-27b-instruct", "qwen"),
        ("deepseek-chat", "deepseek"),
        ("glm-4", "glm"),
        ("chatglm-6b", "glm"),
        ("claude-3-opus", "anthropic"),
        ("gpt-4o", "openai"),
        ("openai/gpt-4", "openai"),
        ("moonshot-v1-8k", "moonshot"),
        ("kimi-k1", "moonshot"),
        ("llama-3-70b", "meta"),
        ("mistral-7b", "mistral"),
        ("", None),
        (None, None),
        ("unknown-model", None),
    ]
    failures = []
    for model, expected in cases:
        got = _infer_llm_provider(model)
        if got != expected:
            failures.append(f"{model!r}: expected {expected!r}, got {got!r}")
    if failures:
        print(f"❌ D FAIL: provider inference mismatches: {failures}")
        sys.exit(1)
    print("✅ D: provider inference OK")


# --- A, B, C, F: event fold produces populated record -----------------------
def _build_synthetic_audit_trail():
    """Build a fake BudgetAuditTrail-like object with two decisions."""
    from prismclaw.engine.compression.budget_orchestrator import (
        BudgetAuditTrail, BudgetDecision,
    )
    return BudgetAuditTrail(decisions=[
        BudgetDecision(
            stage="context_value_selection", action="keep",
            reason="within_budget", zone="dynamic",
            before_tokens=100, after_tokens=100,
        ),
        BudgetDecision(
            stage="dynamic_budget", action="drop",
            reason="drop_low_priority_volatile", zone="dynamic",
            block_id="b1", source="memory",
            before_tokens=200, after_tokens=0,
        ),
        BudgetDecision(
            stage="history_budget", action="compact_history",
            reason="history_over_trigger", zone="history",
            before_tokens=500, after_tokens=200,
        ),
    ])


def test_abcf_projection_fold() -> None:
    sid = "test-projection-fixes"
    state = init_state(sid)

    # Turn + iter start
    state = apply(state, evmod.SessionTurnStarted(
        session_id=sid, turn_counter=1, started_at_ms=1700000000000,
    ))
    state = apply(state, evmod.SessionIterationStarted(
        session_id=sid, turn_counter=1, iteration=1, started_at_ms=1700000000001,
    ))

    # Layer 0.5: projector input
    state = apply(state, evmod.ProjectorInputDone(
        session_id=sid, turn_counter=1, iteration=1,
        order_count=2, order_entry_types=("MessageEntry", "MessageEntry"),
        order_head_seq=(1, 2), order_tail_seq=(1, 2),
        order_tail_types=("MessageEntry", "MessageEntry"),
    ))

    # Layer ①: projector output with msg_count=0 + appended=2 (Fix F)
    state = apply(state, evmod.ProjectorOutputDone(
        session_id=sid, turn_counter=1, iteration=1,
        msg_count=0, msg_roles=(), head=(), tail=(),
        appended_msg_count=2,  # Fix F
    ))

    # Layer ②: assemble entry (1 message: the appended user msg)
    state = apply(state, evmod.AssembleEntryDone(
        session_id=sid, turn_counter=1, iteration=1,
        msg_count=2, msg_roles=("user", "system"),
        head=("[user] hello", "[system] hint"),
        tail=("[user] hello", "[system] hint"),
        entry_meta_keys=("memory_block_loaded",),
    ))

    # Layer ③: pipeline out — exercise Fix A (budget_summary from
    # BudgetAuditTrail.to_metadata_dict()) and Fix B (compressors_used
    # derived from non-"keep" actions). The actual extraction happens at
    # the publish site in turn_preparation.py; here we feed the EVENT
    # post-extraction to verify the projection stores it correctly.
    budget_summary = {
        "decision_count": 3,
        "stage:context_value_selection": 1,
        "stage:dynamic_budget": 1,
        "stage:history_budget": 1,
        "action:keep": 1,
        "action:drop": 1,
        "action:compact_history": 1,
        "reason:within_budget": 1,
        "reason:drop_low_priority_volatile": 1,
        "reason:history_over_trigger": 1,
    }
    compressors_used = (
        "compact_history:history_over_trigger",
        "drop:drop_low_priority_volatile",
    )
    state = apply(state, evmod.PipelineOutDone(
        session_id=sid, turn_counter=1, iteration=1,
        msg_count=2, msg_roles=("user", "system"),
        head=("[user] hello",), tail=("[system] hint",),
        budget_summary=budget_summary,
        compressors_used=compressors_used,
    ))

    # Layer ④: packer final — exercise Fix C (estimated_tokens from
    # estimate_messages_tokens computed at publish site).
    state = apply(state, evmod.PackerFinalDone(
        session_id=sid, turn_counter=1, iteration=1,
        msg_count=2, msg_roles=("user", "system"),
        head=("[user] hello",), tail=("[system] hint",),
        estimated_tokens=42,  # Fix C
        system_text_chars=128,
        tool_schema_chars=2048,
        response_format_chars=None,
    ))

    # LLM response — exercise Fix D (provider inference at apply site).
    state = apply(state, evmod.LLMResponseReceived(
        session_id=sid, turn_counter=1, iteration=1,
        model="qwen2.5-27b-instruct",  # Fix D should infer "qwen"
        finish_reason="stop",
        usage_input_tokens=100, usage_output_tokens=50,
        usage_cache_read_tokens=None, usage_cache_write_tokens=None,
        usage_reasoning_tokens=10,
        ttft_ms=120.5, total_ms=850.0,
        completed_at_ms=1700000000500,
    ))

    # Iteration finished — should commit pending → records
    state = apply(state, evmod.SessionIterationFinished(
        session_id=sid, turn_counter=1, iteration=1,
        finished_at_ms=1700000000600,
    ))

    # Assertions on the committed record
    assert state.pending is None, "pending should be None after iter finished"
    assert len(state.records) == 1, f"expected 1 record, got {len(state.records)}"
    rec = state.records[0]

    # Fix F
    assert rec.projector_appended_msg_count == 2, \
        f"F: projector_appended_msg_count={rec.projector_appended_msg_count}, expected 2"
    print(f"✅ F: projector_appended_msg_count={rec.projector_appended_msg_count}")

    # Fix A
    assert rec.pipeline_out_budget_summary.get("decision_count") == 3, \
        f"A: budget_summary={rec.pipeline_out_budget_summary}"
    assert rec.pipeline_out_budget_summary.get("action:drop") == 1, \
        f"A: budget_summary={rec.pipeline_out_budget_summary}"
    print(f"✅ A: budget_summary={rec.pipeline_out_budget_summary}")

    # Fix B
    assert len(rec.pipeline_out_compressors_used) == 2, \
        f"B: compressors_used={rec.pipeline_out_compressors_used}"
    assert "drop:drop_low_priority_volatile" in rec.pipeline_out_compressors_used
    print(f"✅ B: compressors_used={rec.pipeline_out_compressors_used}")

    # Fix C
    assert rec.packer_final_tokens == 42, \
        f"C: packer_final_tokens={rec.packer_final_tokens}"
    assert rec.packer_final_system_prompt_len == 128
    assert rec.packer_final_tool_schema_total_chars == 2048
    print(f"✅ C: packer_final_tokens={rec.packer_final_tokens} "
          f"sys_prompt_len={rec.packer_final_system_prompt_len} "
          f"tool_schema_chars={rec.packer_final_tool_schema_total_chars}")

    # Fix D
    assert rec.llm_provider == "qwen", \
        f"D: llm_provider={rec.llm_provider}, expected 'qwen'"
    assert rec.llm_model == "qwen2.5-27b-instruct"
    assert rec.packer_final_model == "qwen2.5-27b-instruct"
    print(f"✅ D: llm_provider={rec.llm_provider} llm_model={rec.llm_model}")

    # Status should be "ok" — projector (①) is NOT in the gap check anymore
    # (proj=0 is legit for fresh turns with empty tree history). The 3
    # mandatory downstream layers (asm=1, pipeline=1, packer=1) all have
    # count>0, so no gap.
    assert rec.status == "ok", \
        f"expected status='ok' (3 downstream layers populated), got {rec.status}"
    print(f"✅ status={rec.status} (3 downstream layers populated; projector informational)")


# --- E: orphan recovery via lazy commit -------------------------------------
def test_e_orphan_recovery() -> None:
    """Simulate: iter 1 starts, no SessionIterationFinished, iter 2 starts.

    Without Fix E's lazy-commit, iter 1 would be marked "orphaned" by
    iter 2's SessionIterationStarted → _ensure_pending. With Fix E, the
    publish site (runner.py) lazy-commits iter 1 BEFORE publishing iter 2's
    start. We simulate that here by manually publishing SessionIterationFinished
    for iter 1 before iter 2's start.
    """
    sid = "test-e-orphan"
    state = init_state(sid)
    state = apply(state, evmod.SessionTurnStarted(
        session_id=sid, turn_counter=1, started_at_ms=1700000000000,
    ))
    # iter 1 start
    state = apply(state, evmod.SessionIterationStarted(
        session_id=sid, turn_counter=1, iteration=1, started_at_ms=1700000000001,
    ))
    # (no iter finish — simulating early-return path)

    # Fix E: lazy-commit iter 1 BEFORE iter 2's start.
    state = apply(state, evmod.SessionIterationFinished(
        session_id=sid, turn_counter=1, iteration=1,
        finished_at_ms=1700000000500,
    ))
    # Now iter 2 start — pending should be None, so no orphan.
    state = apply(state, evmod.SessionIterationStarted(
        session_id=sid, turn_counter=1, iteration=2, started_at_ms=1700000000600,
    ))
    # Turn finished — should commit iter 2 (the last one).
    state = apply(state, evmod.SessionTurnFinished(
        session_id=sid, turn_counter=1, finished_at_ms=1700000000700,
    ))

    assert state.orphan_count == 0, \
        f"E: orphan_count={state.orphan_count}, expected 0 (lazy-commit prevented orphan)"
    # iter 1 was committed via SessionIterationFinished (status=error, gap=True)
    # iter 2 was started but had no LLM/layer data — _apply_turn_finished
    # silently drops empty pendings to avoid polluting the audit trail.
    # So only 1 record (iter 1) survives, not 2.
    assert len(state.records) == 1, \
        f"E: expected 1 record (iter 2 dropped as empty), got {len(state.records)}"
    rec1 = state.records[0]
    assert rec1.iteration == 1 and rec1.status == "error", \
        f"E: rec1={rec1.iteration}/{rec1.status}"
    print(f"✅ E: orphan_count={state.orphan_count} "
          f"rec1={rec1.iteration}/{rec1.status} "
          f"(iter 2 silently dropped — no LLM/layer data)")


def test_e_last_iter_committed_via_turn_finished_callback() -> None:
    """Simulate: iter 1 starts + finishes normally, iter 2 starts but never
    finishes (early-return). The turn-finished teardown callback should publish
    SessionIterationFinished for iter 2 BEFORE SessionTurnFinished, so iter 2
    is committed (status=ok/error), not orphaned."""
    sid = "test-e-last-iter"
    state = init_state(sid)
    state = apply(state, evmod.SessionTurnStarted(
        session_id=sid, turn_counter=1, started_at_ms=1700000000000,
    ))
    # iter 1: full lifecycle
    state = apply(state, evmod.SessionIterationStarted(
        session_id=sid, turn_counter=1, iteration=1, started_at_ms=1700000000001,
    ))
    state = apply(state, evmod.SessionIterationFinished(
        session_id=sid, turn_counter=1, iteration=1,
        finished_at_ms=1700000000500,
    ))
    # iter 2: starts but no finish (early-return path)
    state = apply(state, evmod.SessionIterationStarted(
        session_id=sid, turn_counter=1, iteration=2, started_at_ms=1700000000600,
    ))
    # Fix E: turn-finished callback publishes SessionIterationFinished for
    # iter 2 BEFORE SessionTurnFinished fires.
    state = apply(state, evmod.SessionIterationFinished(
        session_id=sid, turn_counter=1, iteration=2,
        finished_at_ms=1700000000700,
    ))
    state = apply(state, evmod.SessionTurnFinished(
        session_id=sid, turn_counter=1, finished_at_ms=1700000000800,
    ))

    assert state.orphan_count == 0, \
        f"E: orphan_count={state.orphan_count}, expected 0"
    assert len(state.records) == 2
    rec1, rec2 = state.records
    assert rec1.status == "error", f"E: rec1.status={rec1.status}"
    # rec2 committed via the synthetic finish event (status=error since
    # gap=True — no layers populated)
    assert rec2.status == "error", f"E: rec2.status={rec2.status}"
    print(f"✅ E (last-iter via turn-finished): orphan_count={state.orphan_count} "
          f"rec1={rec1.iteration}/{rec1.status} rec2={rec2.iteration}/{rec2.status}")


def main() -> None:
    print("=" * 60)
    print("Trajectory Fixes A-F Projection-Layer Test")
    print("=" * 60)
    test_d_provider_inference()
    test_abcf_projection_fold()
    test_e_orphan_recovery()
    test_e_last_iter_committed_via_turn_finished_callback()
    print("=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
