# Approved catalog

`catalog.yaml` is the human-editable approved catalog. It
contains 135 canonical lesson owners and 140 retained teaching points across
32 families: 79 grammar, 18 phraseology, 16
communicative, 11 pronunciation, and 11 writing owners. The current YAML is
the durable result of the catalog decisions; scratch review receipts are not
part of the authoring source.

This is authoring data, not generated lesson JSON. Lesson generation and
curriculum sequencing must still validate each entry and preserve the human
artifact gate. The approved catalog is the scheduling authority; optional
`owner_scope.status: atomic` evidence is copied into plans when present. The
older entries predate that optional field, so the planner reports missing
scope counts instead of silently inventing scope or blocking the complete
human-approved catalog. Split, family-only, or unresolved owners must be
resolved before they enter this approved YAML. Contexts used inside examples
remain ordinary lesson content and are not separate catalog owners.

The current lifecycle is `complete`. Generic family rows remain taxonomy
buckets; they are not lesson owners. Future catalog changes should update this
YAML through the catalog command and keep any review evidence in ignored
scratch output.
