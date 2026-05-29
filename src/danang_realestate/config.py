from pathlib import Path

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

    @property
    def db_dir(self) -> Path:
        return Path(self.duckdb_path).parent

settings = Settings()
