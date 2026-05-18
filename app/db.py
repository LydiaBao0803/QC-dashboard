import os
from typing import Any, Mapping, Optional, Sequence

import pandas as pd
import psycopg
from psycopg.rows import dict_row
import streamlit as st

from .config import DbConfig, load_db_config


def is_demo_mode() -> bool:
    """Return True when demo mode should be used.

    Three ways demo mode activates (in order):
    1. DEMO_MODE env-var is set to a truthy value ("1", "true", "yes").
    2. DEMO_MODE Streamlit secret is set to a truthy value.
    3. Auto-detect: no DB_HOST is configured in either env-var or Streamlit
       secrets — meaning there is no real database to connect to.

    Streamlit Cloud stores secrets in st.secrets (TOML), NOT in os.environ,
    so we check both places for every key.
    """
    _truthy = ("1", "true", "yes")

    # 1. Explicit DEMO_MODE env-var
    if os.environ.get("DEMO_MODE", "").strip().lower() in _truthy:
        return True

    # 2. Explicit DEMO_MODE Streamlit secret
    try:
        import streamlit as st
        if str(st.secrets.get("DEMO_MODE", "")).strip().lower() in _truthy:
            return True
    except Exception:
        pass

    # 3. Auto-detect: if no DB_HOST is present anywhere, fall back to demo mode.
    has_db_host_env = bool(os.environ.get("DB_HOST"))
    has_db_host_secret = False
    try:
        import streamlit as st
        has_db_host_secret = bool(st.secrets.get("DB_HOST"))
    except Exception:
        pass

    if not has_db_host_env and not has_db_host_secret:
        return True

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

