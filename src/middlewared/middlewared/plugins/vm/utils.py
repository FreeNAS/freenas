import contextlib
import os
from xml.etree import ElementTree as etree


ACTIVE_STATES = ['RUNNING', 'SUSPENDED']
SYSTEM_NVRAM_FOLDER_PATH = '/data/subsystems/vm/nvram'
LIBVIRT_URI = 'qemu+unix:///system?socket=/run/truenas_libvirt/libvirt-sock'
LIBVIRT_USER = 'libvirt-qemu'
NGINX_PREFIX = '/vm/display'


def create_element(*args, **kwargs):
    attribute_dict = kwargs.pop('attribute_dict', {})
    element = etree.Element(*args, **kwargs)
    element.text = attribute_dict.get('text')
    element.tail = attribute_dict.get('tail')
    for child in attribute_dict.get('children', []):
        element.append(child)
    return element


def get_virsh_command_args():
    return ['virsh', '-c', LIBVIRT_URI]


def convert_pci_id_to_vm_pci_slot(pci_id: str) -> str:
    return f'pci_{pci_id.replace(".", "_").replace(":", "_")}'


def get_pci_device_class(pci_path: str) -> str:
    with contextlib.suppress(FileNotFoundError):
        with open(os.path.join(pci_path, 'class'), 'r') as r:
            return r.read().strip()

    return ''


def get_vm_nvram_file_name(vm_data: dict) -> str:
    return f'{vm_data["id"]}_{vm_data["name"]}_VARS.fd'
