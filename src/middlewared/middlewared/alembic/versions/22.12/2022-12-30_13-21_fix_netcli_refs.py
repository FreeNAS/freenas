"""fix netcli references

Revision ID: 5cc601ce9a8e
Revises: 136adf794fed
Create Date: 2022-12-30 13:21:11.005256+00:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5cc601ce9a8e'
down_revision = '136adf794fed'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(
        'UPDATE account_bsdusers SET bsdusr_shell = "/usr/sbin/nologin" WHERE bsdusr_shell LIKE "%netcli%"'
    )

def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    pass
    # ### end Alembic commands ###
