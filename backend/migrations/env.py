"""Окружение Alembic: адрес базы — из настроек приложения, схема для autogenerate — app.db.metadata."""
from logging.config import fileConfig

from alembic import context

from app import config as app_config
from app import db

if context.config.config_file_name and context.config.attributes.get("configure_logger", True):
    fileConfig(context.config.config_file_name)

url = context.config.attributes.get("url") or app_config.get().database_url

if context.is_offline_mode():
    context.configure(url=url, target_metadata=db.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with db.engine(url).connect() as con:
        context.configure(connection=con, target_metadata=db.metadata)
        with context.begin_transaction():
            context.run_migrations()
