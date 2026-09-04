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
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    # Direct OpenAI (traffic to api.openai.com via HideMyName split VPN on server)
    openai_parse_model: str = "gpt-4o-mini"
    openai_whisper_model: str = "whisper-1"
    openai_http_referer: str = "https://github.com/solovyshka/food_checking"
    openai_app_title: str = "food_checking"
    hideme_vpn_enabled: bool = True
    hideme_vpn_script: str = "/opt/food_checking/deploy/vpn/hideme-openai.sh"
    hideme_ovpn_conf: str = "/opt/secrets/food_checking/vpn/netherlands-split.ovpn"
    tz: str = "Europe/Moscow"
    app_host: str = "127.0.0.1"
    app_port: int = 8088

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key.strip())

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
