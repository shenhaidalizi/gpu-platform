from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Workload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["batch", "terminal", "jupyter"] = "batch"
    cwd: str = Field(min_length=1, max_length=4096)
    argv: list[str] = Field(min_length=1, max_length=256)
    gpu_count: int = Field(default=1, ge=0)


    @model_validator(mode="after")
    def validate_spec(self):
        if not PurePosixPath(self.cwd).is_absolute() or "\x00" in self.cwd:
            raise ValueError("工作目录需要绝对路径")
        if not self.argv[0] or any("\x00" in arg for arg in self.argv):
            raise ValueError("无效命令")
        if sum(map(len, self.argv)) > 65536:
            raise ValueError("命令过长")
        if self.gpu_count == 0 and self.kind != "terminal":
            raise ValueError("仅普通终端可申请零张卡")
        return self


class Submission(Workload):
    @model_validator(mode="before")
    @classmethod
    def discard_legacy_budget(cls, data):
        if isinstance(data, dict) and "gpu_memory_mib" in data:
            return {key: value for key, value in data.items() if key != "gpu_memory_mib"}
        return data
