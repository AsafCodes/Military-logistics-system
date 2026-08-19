# Import the application's metadata so autogenerate sees every model.
# backend.database calls load_dotenv() via backend.security, so DATABASE_URL
# resolves the same way it does for the running app.
import os
import sys
from logging.config import fileConfig

from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import models  # noqa: F401  (registers every table on Base)
from backend.database import DATABASE_URL, Base, engine

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# The ini file carries no credentials; the runtime environment is the source of truth.
# Only offline mode reads it -- online mode uses the application's own engine.
config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))

# Only the standalone `alembic` CLI should touch logging. When the app calls
# alembic in-process, fileConfig's disable_existing_loggers would tear down
# uvicorn's already-configured loggers.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Reuse a connection the caller already opened, if there is one, so an
    # in-process upgrade does not build a second Engine per alembic command.
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    # Standalone CLI: use the application's engine, which already carries the
    # right connect_args for the configured URL.
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
