from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Status(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    OK: _ClassVar[Status]
    DENIED: _ClassVar[Status]
    FAILED: _ClassVar[Status]
    TIMED_OUT: _ClassVar[Status]
    CANCELLED: _ClassVar[Status]
OK: Status
DENIED: Status
FAILED: Status
TIMED_OUT: Status
CANCELLED: Status

class SkillRequest(_message.Message):
    __slots__ = ("skill_id", "task_id", "trace_id", "auth_context", "idempotency_key", "deadline_unix_ms", "input")
    SKILL_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    AUTH_CONTEXT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    DEADLINE_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    skill_id: str
    task_id: str
    trace_id: str
    auth_context: str
    idempotency_key: str
    deadline_unix_ms: int
    input: _struct_pb2.Struct
    def __init__(self, skill_id: _Optional[str] = ..., task_id: _Optional[str] = ..., trace_id: _Optional[str] = ..., auth_context: _Optional[str] = ..., idempotency_key: _Optional[str] = ..., deadline_unix_ms: _Optional[int] = ..., input: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class SkillResponse(_message.Message):
    __slots__ = ("status", "output", "error_detail")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    ERROR_DETAIL_FIELD_NUMBER: _ClassVar[int]
    status: Status
    output: _struct_pb2.Struct
    error_detail: str
    def __init__(self, status: _Optional[_Union[Status, str]] = ..., output: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., error_detail: _Optional[str] = ...) -> None: ...

class SkillChunk(_message.Message):
    __slots__ = ("seq", "data")
    SEQ_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    seq: int
    data: _struct_pb2.Struct
    def __init__(self, seq: _Optional[int] = ..., data: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class CancelRequest(_message.Message):
    __slots__ = ("task_id",)
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    def __init__(self, task_id: _Optional[str] = ...) -> None: ...

class CancelAck(_message.Message):
    __slots__ = ("acknowledged",)
    ACKNOWLEDGED_FIELD_NUMBER: _ClassVar[int]
    acknowledged: bool
    def __init__(self, acknowledged: bool = ...) -> None: ...
