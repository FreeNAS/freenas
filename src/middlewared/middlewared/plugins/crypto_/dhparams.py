import os
import subprocess

from middlewared.service import job, private, Service

DHPARAM_PEM_PATH = '/data/dhparam.pem'


class CertificateService(Service):

    class Config:
        cli_namespace = 'system.certificate'

    @private
    async def dhparam(self):
        return DHPARAM_PEM_PATH


    @private
    @job()
    def dhparam_setup(self, job):
        """Generate dhparam.pem if it doesn't exist, or has no data in it"""
        with open(DHPARAM_PEM_PATH, 'a+') as f:
            if os.stat(DHPARAM_PEM_PATH).st_size > 0:
                return
            subprocess.run(
                ['openssl', 'dhparam', '-rand', '/dev/urandom', '2048'], stdout=f, check=True
            )
