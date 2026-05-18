import os
from typing import Any, Mapping, Optional, Sequence

import pandas as pd
import psycopg
from psycopg.rows import dict_row
import streamlit as st

from .config import DbConfig, load_db_config


def is_demo_mode() -> bool:
    """Return True when DEMO_MODE is set via env-var OR Streamlit secrets.

    Streamlit Cloud stores secrets in st.secrets (TOML), NOT in os.environ,
    so we must check both.
    """
    _truthy = ("1", "true", "yes")
    if os.environ.get("DEMO_MODE", "").strip().lower() in _truthy:
        return True
    try:
        import streamlit as st
        return str(st.secrets.get("DEMO_MODE", "")).strip().lower() in _truthy
    except Exception:
        return False


@st.cache_resource
def get_connection() -> psycopg.Connection:
    """Return a cached PostgreSQL connection for use inside Streamlit."""
    cfg: DbConfig = load_db_config()
    return psycopg.connect(
        host=cfg.host,
        port=cfg.port,
        dbname=cfg.dbname,
        user=cfg.user,
        password=cfg.password,
    )


def fetch_all(sql: str, params: Optional[Sequence[Any]] = None) -> list[Mapping[str, Any]]:
    """Run a SELECT query and return all rows as dict-like mappings."""
    conn = get_connection()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        rows = cur.fetchall()
    return rows


def fetch_one(sql: str, params: Optional[Sequence[Any]] = None) -> Optional[Mapping[str, Any]]:
    """Run a SELECT query and return a single row, or None."""
    conn = get_connection()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
    return row


def fetch_df(sql: str, params: Optional[Sequence[Any]] = None) -> pd.DataFrame:
    """Run a SELECT query and return results as a DataFrame."""
    rows = fetch_all(sql, params)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def execute(sql: str, params: Optional[Sequence[Any]] = None) -> int:
    """Execute a non-SELECT statement and return the affected row count."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        affected = cur.rowcount
    conn.commit()
    return affected

