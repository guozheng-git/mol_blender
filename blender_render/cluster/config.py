"""Administrator-owned service settings, separate from local render configuration."""
from dataclasses import dataclass, field
from pathlib import Path
import re

from .common import Limits, read_json


@dataclass(frozen=True)
class ServerConfig:
    spool: Path
    image: Path
    image_sha256: str
    tokens_file: Path | None = None
    authentication: str = 'token'
    apptainer: str = '/usr/bin/apptainer'
    sbatch: str = '/usr/bin/sbatch'
    squeue: str = '/usr/bin/squeue'
    sacct: str = '/usr/bin/sacct'
    scancel: str = '/usr/bin/scancel'
    srun: str = '/usr/bin/srun'
    partition: str = ''
    account: str = ''
    qos: str = ''
    cpus: int = 8
    memory_mb: int = 16384
    gpus: int = 0
    walltime_seconds: int = 3900
    runtime_home: Path | None = None
    limits: Limits = field(default_factory=Limits)

    def __post_init__(self):
        if self.authentication not in ('token', 'none'):
            raise ValueError('authentication must be token or none')
        if self.authentication == 'token' and self.tokens_file is None:
            raise ValueError('Token authentication requires tokens_file')
        for name in ('spool', 'image', 'tokens_file'):
            if name == 'tokens_file' and self.tokens_file is None:
                continue
            path = Path(getattr(self, name)).expanduser()
            if not path.is_absolute() or any(c in str(path) for c in '\n\r,:'):
                raise ValueError(name + ' must be an absolute path without newline, comma or colon')
            object.__setattr__(self, name, path.resolve())
        if self.runtime_home is not None:
            path = Path(self.runtime_home).expanduser()
            if not path.is_absolute() or any(c in str(path) for c in '\n\r,:'):
                raise ValueError('runtime_home must be an absolute Linux path without comma, colon or newline')
            object.__setattr__(self, 'runtime_home', path.resolve())
        if not re.fullmatch('[0-9a-f]{64}', self.image_sha256):
            raise ValueError('Pin the SIF image_sha256 before serving')
        for name in ('apptainer', 'sbatch', 'squeue', 'sacct', 'scancel', 'srun'):
            if not Path(getattr(self, name)).is_absolute():
                raise ValueError(name + ' must be an absolute executable path')
        for name in ('partition', 'account', 'qos'):
            value = getattr(self, name)
            if value and not re.fullmatch('[A-Za-z0-9_.:-]{1,100}', value):
                raise ValueError('Invalid Slurm ' + name)
        for name in ('cpus', 'memory_mb', 'walltime_seconds'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(name + ' must be positive')
        if type(self.gpus) is not int or self.gpus not in (0, 1):
            raise ValueError('Request zero or one GPU per render')
        if self.walltime_seconds < self.limits.render_timeout + 60:
            raise ValueError('Slurm walltime must exceed render_timeout by at least 60 seconds')

    @classmethod
    def load(cls, path):
        doc = read_json(path, 64 * 1024)
        if not isinstance(doc, dict):
            raise ValueError('Server config must be an object')
        try:
            return cls(**{**doc, 'limits': Limits(**doc.get('limits', {}))})
        except TypeError as exc:
            raise ValueError('Unknown or missing server configuration field') from exc
