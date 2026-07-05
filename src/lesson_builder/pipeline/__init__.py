"""Lesson pipeline package. Entry points:

- ``build_lesson_qa_graph`` (lesson_qa_graph) — shared Lesson-QA back-half as a
  LangGraph: load -> checks -> fix-loop -> regression -> signoff -> human_gate
  -> export.
- ``gate_lesson`` / ``gate_lesson_results`` (checks.gate_manager) — deterministic
  gate used by the graph and by script-style callers.
- ``lesson_to_export`` (lesson_export) — internal/export Lesson projection.
- ``call_llm`` / ``Agent`` (llm.invocation) — model adapter.
"""
