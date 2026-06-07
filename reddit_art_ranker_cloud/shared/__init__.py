"""Storage- and runtime-agnostic core shared by the local pipeline and the
cloud backend: ELO math, the LLM jury client, pool config.

Nothing in here imports SQLite, boto3, or Lambda — so the exact same ranking
logic runs on a laptop (local initial ranking) and in a Lambda (consumer
insertion)."""
