"""Not a check itself — clients for external services.

Each subpackage wraps one external system: ``llm`` is the LLM client boundary
(shared contracts, typed errors, invocation orchestration, job composition,
one module per vendor adapter), ``tts`` the Google Cloud text-to-speech provider,
``storage`` the S3-compatible object API, ``github`` the ``gh`` release
command. Non-service tool and format integrations live under
``lesson_builder.formats``. Provider-neutral policy (release publication decisions,
citation corroboration, lesson source parsing) stays outside this package.
See ``llm/README.md`` for the LLM boundary rules.
"""
