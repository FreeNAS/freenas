from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator, StringConstraints

from middlewared.api.base import BaseModel, ForUpdateMetaclass, NonEmptyString, single_argument_args

from .virt_device import DeviceType, InstanceType


__all__ = [
    'VirtInstanceEntry', 'VirtInstanceCreateArgs', 'VirtInstanceCreateResult', 'VirtInstanceUpdateArgs',
    'VirtInstanceUpdateResult', 'VirtInstanceDeleteArgs', 'VirtInstanceDeleteResult',
    'VirtInstanceStartArgs', 'VirtInstanceStartResult', 'VirtInstanceStopArgs', 'VirtInstanceStopResult',
    'VirtInstanceRestartArgs', 'VirtInstanceRestartResult', 'VirtInstanceImageChoicesArgs',
    'VirtInstanceImageChoicesResult', 'VirtInstanceDeviceListArgs', 'VirtInstanceDeviceListResult',
    'VirtInstanceDeviceAddArgs', 'VirtInstanceDeviceAddResult', 'VirtInstanceDeviceUpdateArgs',
    'VirtInstanceDeviceUpdateResult', 'VirtInstanceDeviceDeleteArgs', 'VirtInstanceDeviceDeleteResult',
]


REMOTE_CHOICES: TypeAlias = Literal['LINUX_CONTAINERS']


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


class IdmapUserNsEntry(BaseModel):
    hostid: int
    maprange: int
    nsid: int


class UserNsIdmap(BaseModel):
    uid: IdmapUserNsEntry
    gid: IdmapUserNsEntry


class VirtInstanceEntry(BaseModel):
    id: str
    name: Annotated[NonEmptyString, StringConstraints(max_length=200)]
    type: InstanceType = 'CONTAINER'
    status: Literal['RUNNING', 'STOPPED', 'UNKNOWN']
    cpu: str | None
    memory: int | None
    autostart: bool
    environment: dict[str, str]
    aliases: list[VirtInstanceAlias]
    image: Image
    userns_idmap: UserNsIdmap | None
    raw: dict | None
    vnc_enabled: bool
    vnc_port: int | None


# Lets require at least 32MiB of reserved memory
# This value is somewhat arbitrary but hard to think lower value would have to be used
# (would most likely be a typo).
# Running container with very low memory will probably cause it to be killed by the cgroup OOM
MemoryType: TypeAlias = Annotated[int, Field(strict=True, ge=33554432)]


@single_argument_args('virt_instance_create')
class VirtInstanceCreateArgs(BaseModel):
    name: Annotated[NonEmptyString, StringConstraints(max_length=200)]
    source_type: Literal[None, 'IMAGE'] = 'IMAGE'
    image: Annotated[NonEmptyString, StringConstraints(max_length=200)] | None = None
    remote: REMOTE_CHOICES = 'LINUX_CONTAINERS'
    instance_type: InstanceType = 'CONTAINER'
    environment: dict[str, str] | None = None
    autostart: bool | None = True
    cpu: str | None = None
    devices: list[DeviceType] | None = None
    memory: MemoryType | None = None
    enable_vnc: bool = False
    vnc_port: int | None = Field(ge=5900, le=65535, default=None)

    @model_validator(mode='after')
    def validate_attrs(self):
        if self.instance_type == 'CONTAINER':
            if self.source_type != 'IMAGE':
                raise ValueError('Source type must be set to "IMAGE" when instance type is CONTAINER')
            if self.enable_vnc:
                raise ValueError('VNC is not supported for containers and `enable_vnc` should be unset')
        else:
            if self.enable_vnc and self.vnc_port is None:
                raise ValueError('VNC port must be set when VNC is enabled')

        if self.source_type == 'IMAGE' and self.image is None:
            raise ValueError('Image must be set when source type is "IMAGE"')

        return self


class VirtInstanceCreateResult(BaseModel):
    result: VirtInstanceEntry


class VirtInstanceUpdate(BaseModel, metaclass=ForUpdateMetaclass):
    environment: dict[str, str] | None = None
    autostart: bool | None = None
    cpu: str | None = None
    memory: MemoryType | None = None
    vnc_port: int | None = Field(ge=5900, le=65535)


class VirtInstanceUpdateArgs(BaseModel):
    id: str
    virt_instance_update: VirtInstanceUpdate


class VirtInstanceUpdateResult(BaseModel):
    result: VirtInstanceEntry


class VirtInstanceDeleteArgs(BaseModel):
    id: str


class VirtInstanceDeleteResult(BaseModel):
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
    stop_args: StopArgs = StopArgs()


class VirtInstanceStopResult(BaseModel):
    result: bool


class VirtInstanceRestartArgs(BaseModel):
    id: str
    stop_args: StopArgs = StopArgs()


class VirtInstanceRestartResult(BaseModel):
    result: bool


class VirtInstanceImageChoices(BaseModel):
    remote: REMOTE_CHOICES = 'LINUX_CONTAINERS'


class VirtInstanceImageChoicesArgs(BaseModel):
    virt_instances_image_choices: VirtInstanceImageChoices = VirtInstanceImageChoices()


class ImageChoiceItem(BaseModel):
    label: str
    os: str
    release: str
    archs: list[str]
    variant: str
    instance_types: list[InstanceType]


class VirtInstanceImageChoicesResult(BaseModel):
    result: dict[str, ImageChoiceItem]


class VirtInstanceDeviceListArgs(BaseModel):
    id: str


class VirtInstanceDeviceListResult(BaseModel):
    result: list[DeviceType]


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