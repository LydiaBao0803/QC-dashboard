import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str


def load_db_config() -> DbConfig:
    """Load database connection settings from environment variables."""
    return DbConfig(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5434")),
        dbname=os.environ.get("DB_NAME", "assay_qc"),
        user=os.environ.get("DB_USER", "baoyingzhi"),
        password=os.environ.get("DB_PASSWORD", ""),
    )

