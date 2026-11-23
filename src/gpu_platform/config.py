import hashlib
import os
import pwd
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

GPU7 = "GPU-0fb4b2c7-a39e-2032-c5c9-3eabf97816ba"


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    socket: Path
    gpu_uuids: tuple[str, ...]
    system_mode: bool
    api_uid: int
    host: str
    port: int
    users: dict[str, str]
    interpreters: dict[str, str] = field(default_factory=dict)
    shared_limit: int = 2
    shared_headroom_mib: int = 2048
    jupyter_idle_seconds: int = 1800
    lan_host: str | None = None


def config_path():
    return Path(os.environ.get("GPU_PLATFORM_CONFIG", "~/.config/gpu-platform/config.toml")).expanduser()


def load():
    path = config_path()
    with path.open("rb") as source:
        cfg = tomllib.load(source)
    state = Path(cfg["state_dir"]).expanduser()
    devices = tuple(cfg["gpu_uuids"])
    if not devices or len(set(devices)) != len(devices):
        raise ValueError("gpu_uuids must contain distinct device UUIDs")
    users = cfg["users"]
    for user, digest in users.items():
        if pwd.getpwnam(user).pw_uid == 0 or len(bytes.fromhex(digest)) != 32:
            raise ValueError("Invalid user or token digest")
    interpreters = cfg.get("interpreters", {})
    if any(name not in users or not Path(binary).is_absolute() for name, binary in interpreters.items()):
        raise ValueError("interpreters must map enrolled users to absolute paths")
    limit = cfg.get("shared_limit", 2)
    headroom = cfg.get("shared_headroom_mib", 2048)
    if type(limit) is not int or limit < 1 or type(headroom) is not int or headroom < 0:
        raise ValueError("invalid GPU sharing configuration")
    idle = cfg.get("jupyter_idle_seconds", 1800)
    if type(idle) is not int or idle < 1:
        raise ValueError("jupyter_idle_seconds must be a positive integer")
    return Settings(state, Path(cfg["socket"]).expanduser(), devices,
                    cfg.get("system_mode", False), cfg["api_uid"],
                    cfg.get("host", "127.0.0.1"), cfg.get("port", 8765), users, interpreters, limit, headroom, idle,
                    cfg.get("lan_host"))


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()
