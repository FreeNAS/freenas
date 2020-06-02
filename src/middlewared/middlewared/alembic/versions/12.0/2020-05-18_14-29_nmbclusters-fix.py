"""remove nmbclusters sysctl

Revision ID: 730c995cbd37
Revises: 434ea5397cd3
Create Date: 2020-05-18 14:29:59.887895+00:00

"""
from alembic import op
import subprocess as subp
from middlewared.utils import osc


# revision identifiers, used by Alembic.
revision = '730c995cbd37'
down_revision = '434ea5397cd3'
branch_labels = None
depends_on = None

TABLE = 'system_tunable'


def get_db_sysctl_value():

    conn = op.get_bind()

    db_data = conn.execute(
        f'SELECT * FROM {TABLE}'
        ' WHERE tun_var = "kern.ipc.nmbclusters"'
        ' AND tun_comment = "Generated by autotune"'
    ).fetchall()

    data = {}
    for i in db_data:
        # we only care if this is a sysctl type value
        # because a loader type will not have been
        # set by the autotune script and would have
        # been explicitly set by the end-user so leave
        # it alone otherwise
        if i['tun_type'] == 'sysctl':
            data['value'] = i['tun_value']
            data['id'] = i['id']
            return data

    return data


def get_os_sysctl_value():

    curr_val = None

    cp = subp.run(
        ['sysctl', 'kern.ipc.nmbclusters'],
        stdout=subp.PIPE,
        stderr=subp.PIPE,
    )

    if cp.returncode:
        return curr_val

    if cp.stdout:
        try:
            curr_val = int(cp.stdout.decode().strip().split(':')[-1])
        except Exception:
            pass

    return curr_val


def remove_db_entry(db_value, os_value):

    if int(db_value['value']) < os_value:
        conn = op.get_bind()
        conn.execute(
            f'DELETE FROM {TABLE}'
            f' WHERE id = {db_value["id"]}'
        )


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###

    # We need to remove "kern.ipc.nmbclusters" sysctl entry
    # in the following scenarios:
    #   1. this is a freeBSD system
    #   2. kern.ipc.nmbclusters is a "sysctl" type entry in the db
    #        AND is < the current OS value
    if osc.IS_FREEBSD:

        db_value = get_db_sysctl_value()
        os_value = get_os_sysctl_value()

        if db_value and os_value:
            remove_db_entry(db_value, os_value)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    pass
    # ### end Alembic commands ###
