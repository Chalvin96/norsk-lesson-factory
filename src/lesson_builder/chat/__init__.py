"""Conversational operator surface for the lesson workflow.

``controller.handle_chat_message`` turns natural-language operator messages into
a bounded set of lesson actions executed through the existing QA / improvement
flows; ``repl.run_repl`` drives it as a terminal REPL (``lesson-data chat``).
"""
