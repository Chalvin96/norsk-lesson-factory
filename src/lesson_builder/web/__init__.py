"""Not a check itself — loopback HTTP inbound adapter for the authoring workbench.

`server.create_workbench_server` serves the workbench and delegates content to
application operations. Its requests pass through the Host/Origin guard in
`authority`.
"""
