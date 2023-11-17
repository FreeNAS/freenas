from datetime import datetime
import logging
import subprocess

from middlewared.service import private, Service

logger = logging.getLogger(__name__)


class DMIDecode:
    # DMI information is mostly static so cache it
    CACHE = {
        'bios-release-date': None,
        'ecc-memory': None,
        'baseboard-manufacturer': None,
        'baseboard-product-name': None,
        'system-manufacturer': None,
        'system-product-name': None,
        'system-serial-number': None,
        'system-version': None,
        'has-ipmi': None,
    }

    def info(self):
        if all(v is None for k, v in self.CACHE.items()):
            cp = subprocess.run(['dmidecode', '-t', '0,1,2,16,38'], encoding='utf8', capture_output=True)
            self._parse_dmi(cp.stdout.splitlines())

        return self.CACHE

    def _parse_dmi(self, lines):
        self.CACHE = {i: '' for i in self.CACHE}
        self.CACHE['has-ipmi'] = self.CACHE['ecc-memory'] = False
        _type = None
        for line in lines:
            if 'DMI type 0,' in line:
                _type = 'RELEASE_DATE'
            if 'DMI type 1,' in line:
                _type = 'SYSINFO'
            if 'DMI type 2,' in line:
                _type = 'BBINFO'
            if 'DMI type 38,' in line:
                _type = 'IPMI'

            if not line or ':' not in line:
                # "sections" are separated by the category name and then
                # a newline so ignore those lines
                continue

            sect, val = [i.strip() for i in line.split(':', 1)]
            if sect == 'Release Date':
                self._parse_bios_release_date(val)
            elif sect == 'Manufacturer':
                self.CACHE['system-manufacturer' if _type == 'SYSINFO' else 'baseboard-manufacturer'] = val
            elif sect == 'Product Name':
                self.CACHE['system-product-name' if _type == 'SYSINFO' else 'baseboard-product-name'] = val
            elif sect == 'Serial Number' and _type == 'SYSINFO':
                self.CACHE['system-serial-number'] = val
            elif sect == 'Version' and _type == 'SYSINFO':
                self.CACHE['system-version'] = val
            elif sect == 'I2C Slave Address':
                self.CACHE['has-ipmi'] = True
            elif sect == 'Error Correction Type':
                self.CACHE['ecc-memory'] = 'ECC' in val
                # we break the for loop here since "16" is the last section
                # that gets processed
                break

    def _parse_bios_release_date(self, string):
        parts = string.strip().split('/')
        if len(parts) < 3:
            # dont know what the BIOS is reporting so
            # assume it's invalid
            return

        # Give a best effort to convert to a date object.
        # Searched hundreds of debugs that have been provided
        # via end-users and 99% all reported the same date
        # format, however, there are a couple that had a
        # 2 digit year instead of a 4 digit year...gross
        formatter = '%m/%d/%Y' if len(parts[-1]) == 4 else '%m/%d/%y'
        try:
            self.CACHE['bios-release-date'] = datetime.strptime(string, formatter).date()
        except Exception:
            logger.warning('Failed to format BIOS release date to datetime object', exc_info=True)


class SystemService(Service):
    dmidecode = DMIDecode()

    @private
    def dmidecode_info(self):
        return self.dmidecode.info()