from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    zai_api_key: str
    vision_model: str = "glm-5v-turbo"
    text_model: str = "glm-5"
    ocr_model: str = "glm-ocr"
    max_concurrent: int = 3
    scrape_delay: float = 1.0
    max_image_dimension: int = 2000
    data_dir: Path = Path("data")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TOON_",
        # ZAI_API_KEY is not prefixed with TOON_
        extra="ignore",
    )

    # Override to allow ZAI_API_KEY without prefix
    @classmethod
    def _env_file_settings(cls, init_kwargs, **kwargs):
        return super()._env_file_settings(init_kwargs, **kwargs)


class _Settings(BaseSettings):
    zai_api_key: str
    zai_base_url: str = "https://api.z.ai/api/coding/paas/v4"
    toon_vision_model: str = "glm-5v-turbo"
    toon_text_model: str = "glm-5"
    toon_ocr_model: str = "glm-ocr"
    toon_max_concurrent: int = 2
    toon_scrape_delay: float = 1.0
    toon_max_image_dimension: int = 2000
    toon_data_dir: Path = Path("data")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def vision_model(self) -> str:
        return self.toon_vision_model

    @property
    def text_model(self) -> str:
        return self.toon_text_model

    @property
    def ocr_model(self) -> str:
        return self.toon_ocr_model

    @property
    def max_concurrent(self) -> int:
        return self.toon_max_concurrent

    @property
    def scrape_delay(self) -> float:
        return self.toon_scrape_delay

    @property
    def max_image_dimension(self) -> int:
        return self.toon_max_image_dimension

    @property
    def data_dir(self) -> Path:
        return self.toon_data_dir


def get_settings() -> _Settings:
    return _Settings()
