"""Shot-budget policy plugin configuration (plugin-local, per repo convention)."""

from pydantic_settings import BaseSettings


class ShotBudgetSettings(BaseSettings):
    # Concurrent-active shots granted per held license. The per-principal
    # budget is min(n_licenses x SHOTS_PER_LICENSE, MAX_CONCURRENT_SHOTS).
    SHOTS_PER_LICENSE: int = 625000
