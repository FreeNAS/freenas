from typing import Literal, List, Union, TypeAlias
from typing_extensions import Annotated

from pydantic import Field, StringConstraints

from middlewared.api.base import (
    BaseModel, ForUpdateMetaclass, NonEmptyString,
    LocalGID, LocalUID,
    single_argument_args, single_argument_result,
)


class VirtGlobalEntry(BaseModel):
    id: int
    pool: str | None = None
    dataset: str | None = None
    bridge: str | None = None
    v4_network: str | None = None
    v6_network: str | None = None
    state: Literal['INITIALIZING', 'INITIALIZED', 'NO_POOL', 'ERROR', 'LOCKED'] | None = None


@single_argument_args('virt_global_update')
class VirtGlobalUpdateArgs(BaseModel, metaclass=ForUpdateMetaclass):
    pool: NonEmptyString | None = None
    bridge: NonEmptyString | None = None
    v4_network: str | None = None
    v6_network: str | None = None


class VirtGlobalUpdateResult(BaseModel):
    result: VirtGlobalEntry


class VirtGlobalBridgeChoicesArgs(BaseModel):
    pass


class VirtGlobalBridgeChoicesResult(BaseModel):
    result: dict


class VirtGlobalPoolChoicesArgs(BaseModel):
    pass


class VirtGlobalPoolChoicesResult(BaseModel):
    result: dict


class VirtGlobalGetNetworkArgs(BaseModel):
    name: NonEmptyString


@single_argument_result
class VirtGlobalGetNetworkResult(BaseModel):
    type: Literal['BRIDGE']
    managed: bool
    ipv4_address: NonEmptyString
    ipv4_nat: bool
    ipv6_address: NonEmptyString
    ipv6_nat: bool


REMOTE_CHOICES: TypeAlias = Literal['LINUX_CONTAINERS']
InstanceType: TypeAlias = Literal['CONTAINER', 'VM']


@single_argument_args('virt_instances_image_choices')
class VirtInstanceImageChoicesArgs(BaseModel):
    remote: REMOTE_CHOICES = 'LINUX_CONTAINERS'
    instance_type: InstanceType = 'CONTAINER'


class ImageChoiceItem(BaseModel):
    label: str
    os: str
    release: str
    archs: list[str]
    variant: str


class VirtInstanceImageChoicesResult(BaseModel):
    result: dict[str, ImageChoiceItem]


class Device(BaseModel):
    name: NonEmptyString | None = None
    description: NonEmptyString | None = None
    readonly: bool = False


class Disk(Device):
    dev_type: Literal['DISK']
    source: str | None = None
    destination: str | None = None


NicType: TypeAlias = Literal['BRIDGED', 'MACVLAN']


class NIC(Device):
    dev_type: Literal['NIC']
    network: NonEmptyString | None = None
    nic_type: NicType | None = None
    parent: NonEmptyString | None = None


class USB(Device):
    dev_type: Literal['USB']
    bus: int | None = None
    dev: int | None = None
    product_id: str | None = None
    vendor_id: str | None = None


Proto: TypeAlias = Literal['UDP', 'TCP']


class Proxy(Device):
    dev_type: Literal['PROXY']
    source_proto: Proto
    source_port: int = Field(ge=1, le=65535)
    dest_proto: Proto
    dest_port: int = Field(ge=1, le=65535)


class TPM(Device):
    dev_type: Literal['TPM']
    path: str | None = None
    pathrm: str | None = None


GPUType: TypeAlias = Literal['PHYSICAL', 'MDEV', 'MIG', 'SRIOV']


class GPU(Device):
    dev_type: Literal['GPU']
    gpu_type: GPUType
    id: str | None = None
    gid: LocalGID | None = None
    uid: LocalUID | None = None
    mode: str | None = None
    mdev: NonEmptyString | None = None
    mig_uuid: NonEmptyString | None = None
    pci: NonEmptyString | None = None
    productid: NonEmptyString | None = None
    vendorid: NonEmptyString | None = None


DeviceType: TypeAlias = Annotated[
    Union[Disk, GPU, Proxy, TPM, USB, NIC],
    Field(discriminator='dev_type')
]


class VirtInstanceAlias(BaseModel):
    type: Literal['INET', 'INET6']
    address: NonEmptyString
    netmask: int


class Image(BaseModel):
    architecture: str | None
    description: str | None
    os: str | None
    release: str | None
    serial: str | None
    type: str | None
    variant: str | None


class VirtInstanceEntry(BaseModel):
    id: str
    name: Annotated[NonEmptyString, StringConstraints(max_length=200)]
    type: InstanceType = 'CONTAINER'
    status: Literal['RUNNING', 'STOPPED', 'UNKNOWN']
    cpu: str | None
    memory: int | None
    autostart: bool
    environment: dict[str, str]
    aliases: List[VirtInstanceAlias]
    image: Image
    raw: dict


# Lets require at least 32MiB of reserved memory
# This value is somewhat arbitrary but hard to think lower value would have to be used
# (would most likely be a typo).
# Running container with very low memory will probably cause it to be killed by the cgroup OOM
MemoryType: TypeAlias = Annotated[int, Field(strict=True, ge=33554432)]


@single_argument_args('virt_instance_create')
class VirtInstanceCreateArgs(BaseModel):
    name: Annotated[NonEmptyString, StringConstraints(max_length=200)]
    image: Annotated[NonEmptyString, StringConstraints(max_length=200)]
    remote: REMOTE_CHOICES = 'LINUX_CONTAINERS'
    instance_type: InstanceType = 'CONTAINER'
    environment: dict[str, str] | None = None
    autostart: bool | None = True
    cpu: str | None = None
    devices: List[DeviceType] | None = None
    memory: MemoryType | None = None


class VirtInstanceCreateResult(BaseModel):
    result: VirtInstanceEntry


class VirtInstanceUpdate(BaseModel, metaclass=ForUpdateMetaclass):
    environment: dict[str, str] | None = None
    autostart: bool | None = None
    cpu: str | None = None
    memory: MemoryType | None = None


class VirtInstanceUpdateArgs(BaseModel):
    id: str
    virt_instance_update: VirtInstanceUpdate


class VirtInstanceUpdateResult(BaseModel):
    result: VirtInstanceEntry


class VirtInstanceDeleteArgs(BaseModel):
    id: str


class VirtInstanceDeleteResult(BaseModel):
    result: Literal[True]


class VirtInstanceDeviceListArgs(BaseModel):
    id: str


class VirtInstanceDeviceListResult(BaseModel):
    result: List[DeviceType]


class VirtInstanceDeviceAddArgs(BaseModel):
    id: str
    device: DeviceType


class VirtInstanceDeviceAddResult(BaseModel):
    result: Literal[True]


class VirtInstanceDeviceUpdateArgs(BaseModel):
    id: str
    device: DeviceType


class VirtInstanceDeviceUpdateResult(BaseModel):
    result: Literal[True]


class VirtInstanceDeviceDeleteArgs(BaseModel):
    id: str
    name: str


class VirtInstanceDeviceDeleteResult(BaseModel):
    result: Literal[True]


class VirtInstanceStartArgs(BaseModel):
    id: str


class VirtInstanceStartResult(BaseModel):
    result: bool


class StopArgs(BaseModel):
    timeout: int = -1
    force: bool = False


class VirtInstanceStopArgs(BaseModel):
    id: str
    stop_args: StopArgs


class VirtInstanceStopResult(BaseModel):
    result: bool


class VirtInstanceRestartArgs(BaseModel):
    id: str
    stop_args: StopArgs


class VirtInstanceRestartResult(BaseModel):
    result: bool


class VirtDeviceUSBChoicesArgs(BaseModel):
    pass


class USBChoice(BaseModel):
    vendor_id: str
    product_id: str
    bus: int
    dev: int
    product: str
    manufacturer: str


class VirtDeviceUSBChoicesResult(BaseModel):
    result: dict[str, USBChoice]


class VirtDeviceGPUChoicesArgs(BaseModel):
    instance_type: InstanceType
    gpu_type: GPUType


class GPUChoice(BaseModel):
    bus: int
    slot: int
    description: str
    vendor: str | None = None
    pci: str


class VirtDeviceGPUChoicesResult(BaseModel):
    result: dict[str, GPUChoice]


class VirtDeviceDiskChoicesArgs(BaseModel):
    pass


class VirtDeviceDiskChoicesResult(BaseModel):
    result: dict[str, str]


class VirtDeviceNICChoicesArgs(BaseModel):
    nic_type: NicType


class VirtDeviceNICChoicesResult(BaseModel):
    result: dict[str, str]


class VirtImageUploadArgs(BaseModel):
    pass


@single_argument_result
class VirtImageUploadResult(BaseModel):
    fingerprint: NonEmptyString
    size: int