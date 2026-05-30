from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Geocoding
    goong_api_key: str = ""

    # Database
    duckdb_path: str = "./data/danang.duckdb"

    # Scraping
    scrape_delay_min: int = 2
    scrape_delay_max: int = 8
    scrape_user_agent: str = (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    )

    # Nominatim
    nominatim_user_agent: str = "danang-realestate/0.1.0 (personal-project)"

    # Postgres serving DB (the copy Metabase reads). Single source of truth for the
    # pipeline's PG connection — orchestration reads these instead of os.getenv. The
    # password has NO baked-in default on purpose: set PG_PASSWORD (or POSTGRES_PASSWORD)
    # in an untracked .env so a real secret never lands in source/compose files.
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_db: str = "danang"
    pg_user: str = "danang"
    # Accept either PG_PASSWORD or POSTGRES_PASSWORD (the latter is what the Postgres
    # container uses) so the secret only has to be defined once in .env.
    pg_password: str = Field(
        default="", validation_alias=AliasChoices("pg_password", "postgres_password")
    )
    pg_schema: str = "public"

    # Alerting (optional). If set, a Dagster failure sensor posts to this Slack
    # incoming-webhook URL when daily_refresh or the schema-drift check fails.
    slack_webhook_url: str = ""

    @property
    def db_dir(self) -> Path:
        return Path(self.duckdb_path).parent

settings = Settings()
