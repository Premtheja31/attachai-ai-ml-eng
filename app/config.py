from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+psycopg://kindred:kindred@localhost:5432/kindred"
    embed_dim: int = 16
    payment_timeout_trigger_cents: int = 999999
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    llm_max_attempts: int = 3
    intro_confidence_threshold: float = 0.6
    matching_confidence_threshold: float = 0.0


settings = Settings()
