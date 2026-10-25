from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MDS_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    database_url: str = "sqlite:///./data/molecular_docking.db"
    redis_url: str = "redis://localhost:6379/0"
    frontend_origin: str = "http://localhost:5173"
    max_upload_mb: int = 100
    command_timeout_seconds: int = 7200
    p2rank_command: str = "prank"
    smina_command: str = "smina"
    gnina_command: str = "gnina"
    redis_server_command: str = "redis-server"
    pdb2pqr_command: str = "pdb2pqr"

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
