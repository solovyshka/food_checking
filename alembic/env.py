"""Alembic config."""

from logging.config import fileConfig
import re

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.models import Base
from app.db import mobile_models  # Register mobile tables for migration metadata.

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata
tenant_schema = context.get_x_argument(as_dictionary=True).get("schema")
if tenant_schema and not re.fullmatch(r"food_user_[a-f0-9]{32}", tenant_schema):
    raise ValueError("Invalid mobile diary schema")


def run_migrations_offline() -> None:
    if tenant_schema:
        raise ValueError("Tenant migrations require a database connection")
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if tenant_schema:
            connection.exec_driver_sql('SET search_path TO "' + tenant_schema + '"')
            connection.commit()
            connection.dialect.default_schema_name = tenant_schema
        context.configure(connection=connection, target_metadata=target_metadata, version_table_schema=tenant_schema)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
