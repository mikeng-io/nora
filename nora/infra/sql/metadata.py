"""The one `MetaData` every `nora.infra.sql` table registers on.

A cycle-free leaf so `schema` and `schema_managed` both bind tables to it no
matter which module an importer reaches first.
"""

import sqlalchemy as sa

METADATA = sa.MetaData()
