"""Entry point: ``JobRunner`` (invocation), ``load_llm_job`` (config), and the role jobs in ``jobs``.

One package per external system: ``base`` holds the shared client contracts,
``exceptions`` the typed error hierarchy, ``config`` the strict root
``config.yaml`` tier/job loader, ``invocation`` the one-client job runner and
retry policy, ``jobs`` the role composition boundary,
and ``opencode`` the single supported CLI transport adapter (model and
provider selection happens through each tier's ``model`` behind OpenCode).
The concrete vendor import stays inside this package's composition boundary;
production callers use jobs or invocation. See ``README.md`` for
the full boundary rules.
"""
