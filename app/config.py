from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""
    database_url: str = "postgresql://food:changeme@127.0.0.1:5432/food_checking"
    whisper_url: str = "http://127.0.0.1:9000"
    whisper_model: str = "small"
    qwen_url: str = "http://127.0.0.1:11434"
    qwen_model: str = "qwen2.5:7b-instruct-q4_K_M"
    ollama_keep_alive: str = "0"
    tz: str = "Europe/Moscow"
    app_host: str = "127.0.0.1"
    app_port: int = 8088

    @property
    def allowed_user_ids(self) -> set[int]:
        ids: set[int] = set()
        for part in self.telegram_allowed_user_ids.split(","):
            part = part.strip()
            if part:
                ids.add(int(part))
        return ids

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)


@lru_cache
def get_settings() -> Settings:
    return Settings()
