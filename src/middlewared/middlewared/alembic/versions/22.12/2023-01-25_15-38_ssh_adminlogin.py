"""ssh_adminlogin

Revision ID: af5efb72c74f
Revises: 004f0934ff0f
Create Date: 2023-01-25 15:38:53.058719+00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'af5efb72c74f'
down_revision = '004f0934ff0f'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('services_ssh', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ssh_adminlogin', sa.Boolean(), nullable=False, server_default="1"))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('services_ssh', schema=None) as batch_op:
        batch_op.drop_column('ssh_adminlogin')

    # ### end Alembic commands ###
