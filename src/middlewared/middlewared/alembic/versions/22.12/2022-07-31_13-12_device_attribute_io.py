"""iotype added

Revision ID: 9b7127606e2b
Revises: b44a453a8b3f
Create Date: 2022-07-31 13:12:33.727477+00:00

"""
import json

from alembic import op


# revision identifiers, used by Alembic.
revision = '9b7127606e2b'
down_revision = 'b44a453a8b3f'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    devices = {
        row['id']: json.loads(row['attributes'])
        for row in map(dict, conn.execute("SELECT * FROM vm_device WHERE dtype IN ('DISK', 'RAW')").fetchall())
    }

    for device_id, device in devices.items():
        device['iotype'] = 'THREADS'
        conn.execute("UPDATE vm_device SET attributes = ? WHERE id = ?", (
            json.dumps(device), device_id
        ))


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    pass
    # ### end Alembic commands ###
