from logging import getLogger

from middlewared.plugins.enclosure_.constants import (
    DISK_FRONT_KEY,
    DISK_REAR_KEY,
    DISK_TOP_KEY,
    DISK_INTERNAL_KEY,
    DRIVE_BAY_LIGHT_STATUS,
    SUPPORTS_IDENTIFY_KEY,
    SUPPORTS_IDENTIFY_STATUS_KEY,
)
from middlewared.plugins.enclosure_.enums import (
    ElementStatus,
    RedfishStatusHealth,
    RedfishStatusState
)
from middlewared.plugins.enclosure_.slot_mappings import get_jbof_slot_info

LOGGER = getLogger(__name__)


def fake_jbof_enclosure(model, uuid, num_of_slots, mapped, ui_info, elements={}, drive_bay_light_status={}):
    """This function takes the nvme devices that been mapped
    to their respective slots and then creates a "fake" enclosure
    device that matches (similarly) to what our real enclosure
    mapping code does (get_ses_enclosures()). It's _VERY_ important
    that the keys in the `fake_enclosure` dictionary exist because
    our generic enclosure mapping logic expects certain top-level
    keys.

    Furthermore, we generate DMI (SMBIOS) information for this
    "fake" enclosure because our enclosure mapping logic has to have
    a guaranteed unique key for each enclosure so it can properly
    map the disks accordingly
    """
    # TODO: The `fake_enclosure` object should be removed from this
    # function and should be generated by the
    # `plugins.enclosure_/enclosure_class.py:Enclosure` class so we
    # can get rid of duplicate logic in this module and in that class
    fake_enclosure = {
        'id': uuid,
        'dmi': uuid,
        'model': model,
        'should_ignore': False,
        'sg': None,
        'bsg': None,
        'name': f'{model} JBoF Enclosure',
        'controller': False,
        'status': ['OK'],
        'elements': {'Array Device Slot': {}}
    }
    disks_map = get_jbof_slot_info(model)
    if not disks_map:
        fake_enclosure['should_ignore'] = True
        return [fake_enclosure]

    fake_enclosure.update(ui_info)

    for slot in range(1, num_of_slots + 1):
        device = mapped.get(slot, None)
        # the `value_raw` variables represent the
        # value they would have if a device was
        # inserted into a proper SES device (or not).
        # Since this is NVMe (which deals with PCIe)
        # that paradigm doesn't exist per se but we're
        # "faking" a SES device, hence the hex values.
        # The `status` variables use same logic.
        if device is not None:
            status = 'OK'
            value_raw = 0x1000000
        else:
            status = 'Not installed'
            value_raw = 0x5000000

        mapped_slot = disks_map['versions']['DEFAULT']['model'][model][slot]['mapped_slot']
        light = disks_map['versions']['DEFAULT']['model'][model][slot][SUPPORTS_IDENTIFY_KEY]
        dfk = disks_map['versions']['DEFAULT']['model'][model][slot][DISK_FRONT_KEY]
        drk = disks_map['versions']['DEFAULT']['model'][model][slot][DISK_REAR_KEY]
        dtk = disks_map['versions']['DEFAULT']['model'][model][slot][DISK_TOP_KEY]
        dik = disks_map['versions']['DEFAULT']['model'][model][slot][DISK_INTERNAL_KEY]

        # light_status will follow light unless explicitedly overridden
        light_status = disks_map['versions']['DEFAULT']['model'][model][slot].get(SUPPORTS_IDENTIFY_STATUS_KEY, light)
        if light_status:
            led = drive_bay_light_status.get(slot, None)
        else:
            led = None

        fake_enclosure['elements']['Array Device Slot'][mapped_slot] = {
            'descriptor': f'Disk #{slot}',
            'status': status,
            'value': None,
            'value_raw': value_raw,
            'dev': device,
            SUPPORTS_IDENTIFY_KEY: light,
            DISK_FRONT_KEY: dfk,
            DISK_REAR_KEY: drk,
            DISK_TOP_KEY: dtk,
            DISK_INTERNAL_KEY: dik,
            DRIVE_BAY_LIGHT_STATUS: led,
            'original': {
                'enclosure_id': uuid,
                'enclosure_sg': None,
                'enclosure_bsg': None,
                'descriptor': f'slot{slot}',
                'slot': slot,
            }
        }

    for element_type in elements:
        if elements[element_type]:
            fake_enclosure['elements'][element_type] = elements[element_type]

    return [fake_enclosure]


def map_redfish_status_to_status(status):
    """Return a status string based upon the Redfish Status"""

    if state := status.get('State'):
        if state == RedfishStatusState.ABSENT.value:
            return ElementStatus.NOT_INSTALLED.value

    if health := status.get('Health'):
        match health:
            case RedfishStatusHealth.CRITICAL.value:
                return ElementStatus.CRITICAL.value
            case RedfishStatusHealth.OK.value:
                return ElementStatus.OK.value
            case RedfishStatusHealth.WARNING.value:
                return ElementStatus.NONCRITICAL.value
            case _:
                return ElementStatus.UNKNOWN.value
    return ElementStatus.UNKNOWN.value


def map_redfish_to_value(data, keys):
    """Return a value which is a comma seperated string of all the values
    present in data."""
    # It was decided NOT to try to map these to SES-like values, as this
    # would introduce an impedance mismatch when we circle back to the
    # Redfish provider again.
    values = []
    for key in keys:
        if val := data.get(key):
            values.append(val)
    return ', '.join(values) or None


def map_redfish_psu_to_value(psu):
    """Return a value string corresponding to the redfish data"""
    # Just use LineInputStatus (DSP0268_2024.1 6.103.5.2 LineInputStatus)
    return map_redfish_to_value(psu, ['LineInputStatus'])


def map_redfish_psu(psu):
    """Utility function to map a Redfish PSU data to our enclosure services format"""
    # Redfish Data Model Specification https://www.dmtf.org/dsp/DSP0268
    # DSP0268_2024.1 6.103 PowerSupply 1.6.0
    # DSP0268_2023.2 6.103 PowerSupply 1.5.2
    # DSP0268_2023.1 6.97 PowerSupply 1.5.1
    # ...
    # For ES24n implemented with @odata.type = #PowerSupply.v1_5_1.PowerSupply
    #
    # Example data from redfish
    # {'@odata.id': '/redfish/v1/Chassis/2U24/PowerSubsystem/PowerSupplies/PSU1',
    #  '@odata.type': '#PowerSupply.v1_5_1.PowerSupply',
    #  'Actions': {
    #       '#PowerSupply.Reset': {
    #           'ResetType@Redfish.AllowableValues': ['On','ForceOff'],
    #           'target': '/redfish/v1/Chassis/2U24/PowerSubsystem/PowerSupplies/PSU1/Actions/PowerSupply.Reset'
    #       }
    #   },
    #  'FirmwareVersion': 'A00',
    #  'Id': 'PSU1',
    #  'LineInputStatus': 'Normal',
    #  'Manufacturer': '3Y POWER',
    #  'Model': 'YSEF1600EM-2A01P10',
    #  'Name': 'PSU1',
    #  'PowerCapacityWatts': 1600,
    #  'SerialNumber': 'S0A00A3032029000265',
    #  'Status': {'Health': 'OK',
    #             'State': 'Enabled'}},
    desc_fields = ['Name', 'Model', 'SerialNumber', 'FirmwareVersion', 'Manufacturer']
    desc = [psu.get(k, '') for k in desc_fields]
    if watt := psu.get('PowerCapacityWatts'):
        desc.append(f'{watt}W')
    return {
        'descriptor': ','.join(desc),
        "status": map_redfish_status_to_status(psu['Status']),
        "value": map_redfish_psu_to_value(psu),
        "value_raw": None
    }


def map_power_supplies(data):
    result = {}
    for member in data['PowerSubsystem']['PowerSupplies']['Members']:
        ident = member.get('Id')
        if ident:
            result[ident] = map_redfish_psu(member)
    return result


def map_redfish_fan_to_value(data):
    values = []
    if speedpercent := data.get('SpeedPercent'):
        if speedrpm := speedpercent.get('SpeedRPM'):
            values.append(f'SpeedRPM={speedrpm}')
    if location_indicator_active := data.get('LocationIndicatorActive'):
        if location_indicator_active:
            values.append('LocationIndicatorActive')
    return ', '.join(values) or None


def map_redfish_fan(data):
    # Example data from redfish
    # {'@odata.id': '/redfish/v1/Chassis/2U24/ThermalSubsystem/Fans/Fan1',
    #  '@odata.type': '#Fan.v1_4_0.Fan',
    #  'Id': 'Fan1',
    #  'LocationIndicatorActive': False,
    #  'Name': 'Fan1',
    #  'SpeedPercent': {'DataSourceUri': '/redfish/v1/Chassis/2U24/Sensors/Fan1', 'SpeedRPM': 9920.0},
    #  'Status': {'Health': 'OK', 'State': 'Enabled'}}
    return {
        'descriptor': data.get('Name'),
        "status": map_redfish_status_to_status(data['Status']),
        "value": map_redfish_fan_to_value(data),
        "value_raw": None
    }


def map_cooling(data):
    result = {}
    for member in data['ThermalSubsystem']['Fans']['Members']:
        ident = member.get('Id')
        if ident:
            result[ident] = map_redfish_fan(member)
    return result


def map_redfish_sensor_to_value(data):
    if reading := data.get('Reading'):
        if units := data.get('ReadingUnits'):
            return f'{reading} {units}'
        else:
            # Make sure it's a string
            return f'{reading}'


def map_redfish_temperature_sensor(data):
    # Example data from redfish
    # {'@odata.id': '/redfish/v1/Chassis/2U24/Sensors/TempDrive1',
    #  '@odata.type': '#Sensor.v1_6_0.Sensor',
    #  'Id': 'TempDrive1',
    #  'Name': 'Temperature Sensor Drive 1',
    #  'Reading': 26.0,
    #  'ReadingType': 'Temperature',
    #  'ReadingUnits': 'C',
    #  'Status': {'Health': 'OK', 'State': 'Enabled'}},
    return {
        'descriptor': data.get('Name'),
        "status": map_redfish_status_to_status(data['Status']),
        "value": map_redfish_sensor_to_value(data),
        "value_raw": None
    }


def map_temperature_sensors(data):
    result = {}
    for member in data['Sensors']['Members']:
        ident = member.get('Id')
        reading_type = member.get('ReadingType')
        if ident and reading_type == 'Temperature':
            result[ident] = map_redfish_temperature_sensor(member)
    return result


def map_redfish_voltage_sensor(data):
    # Example data from redfish
    # {'@odata.id': '/redfish/v1/Chassis/2U24/Sensors/VoltPS1Vin',
    #  '@odata.type': '#Sensor.v1_6_0.Sensor',
    #  'Id': 'VoltPS1Vin',
    #  'Name': 'VoltPS1Vin',
    #  'Reading': 206.0,
    #  'ReadingType': 'Voltage',
    #  'Status': {'Health': 'OK', 'State': 'Enabled'}},
    return {
        'descriptor': data.get('Name'),
        "status": map_redfish_status_to_status(data['Status']),
        "value": map_redfish_sensor_to_value(data),
        "value_raw": None
    }


def map_voltage_sensors(data):
    result = {}
    for member in data['Sensors']['Members']:
        ident = member.get('Id')
        reading_type = member.get('ReadingType')
        if ident and reading_type == 'Voltage':
            result[ident] = map_redfish_voltage_sensor(member)
    return result
