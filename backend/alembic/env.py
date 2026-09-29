from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import get_settings
from app.db.base import Base
from app.models import (  # noqa: F401 - ensures models are registered on Base
    AnalysisArtifact,
    AnalysisJob,
    Dataset,
    Measurement,
    Project,
    Report,
    User,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)

target_metadata = Base.metadata

# PostGIS installs its own system tables/views (spatial_ref_sys, geometry_columns,
# etc.) that are not part of our application schema and must never be
# dropped/recreated by autogenerate diffs.
POSTGIS_SYSTEM_TABLES = {"spatial_ref_sys", "geometry_columns", "geography_columns"}


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and name in POSTGIS_SYSTEM_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
