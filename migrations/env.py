from alembic import context
from backend.app.models import Base
config = context.config
connection = config.attributes.get('connection')
if connection is None:
    raise RuntimeError('Use ProjectHub startup to migrate the configured application data directory')
context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
