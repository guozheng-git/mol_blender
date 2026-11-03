"""Fixed administrator-selected Slurm/Apptainer commands; never accept shell input."""
import os
import re
import shlex
import subprocess
from datetime import datetime
import pwd
import time


class SubmissionUnknown(RuntimeError):
    """A job may have been accepted. Never resubmit automatically."""


class Slurm:
    def __init__(self, config):
        self.config = config
        self._states = {}
        self._unavailable_until = 0
        self._last_error = None

    def run(self, command):
        # Do not inherit SBATCH_*, APPTAINER_*, Python paths or service credentials.
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
        for name in ('HOME', 'USER', 'LOGNAME', 'SLURM_CONF'):
            if name in os.environ:
                env[name] = os.environ[name]
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=30, check=True)

    def container_command(self, directory):
        c = self.config
        command = [c.apptainer, 'exec', '--cleanenv', '--containall', '--no-home',
                   '--no-mount', 'hostfs,bind-paths,cwd', '--bind', f'{directory}:/job',
                   '--pwd', '/job']
        if c.gpus:
            command.append('--nv')
        runtime = str(c.runtime_home or '/opt/studio-runtime')
        if c.runtime_home:
            command.extend(['--bind', f'{runtime}:{runtime}:ro',
                            '--env', 'STRUCTURE_STUDIO_HOME=' + runtime,
                            '--env', 'BLENDER_RENDER_HOME=' + runtime + '/blender-render'])
        command.extend([str(c.image), runtime + '/render/bin/python', '-I',
                        '-m', 'blender_render.cluster.worker'])
        return command

    def script(self, directory):
        command = self.container_command(directory) + ['/job']
        # srun establishes task CPU affinity and CUDA_VISIBLE_DEVICES before the
        # small fixed shell forwards those settings into the clean container.
        launch = ('set -eu\n'
                  f'export APPTAINERENV_OMP_NUM_THREADS={self.config.cpus}\n'
                  f'export APPTAINERENV_OPENBLAS_NUM_THREADS={self.config.cpus}\n')
        if self.config.gpus:
            launch += 'export APPTAINERENV_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:?Slurm did not assign a GPU}"\n'
        launch += 'exec "$@"'
        task = [self.config.srun, '--export=ALL', '--ntasks=1',
                f'--cpus-per-task={self.config.cpus}', '--cpu-bind=cores',
                '/bin/sh', '-c', launch, 'blender-cluster', *command]
        # Record the compute node's container runtime for acceptance evidence.
        version = shlex.quote(str(self.config.apptainer)) + ' --version > apptainer.version 2>&1 || true\n'
        return '#!/bin/sh\nset -eu\numask 077\n' + version + 'exec ' + shlex.join(task) + '\n'

    def gpu_options(self):
        c = self.config
        if not c.gpus:
            return []
        count = (c.gpu_type + ':1') if c.gpu_type else '1'
        return ['--gres=gpu:' + count] if c.gpu_request == 'gres' else ['--gpus=' + count]

    def batch_options(self, directory, identifier):
        """Every sbatch option of a real submission; shared with the doctor dry run."""
        c = self.config
        hours, remainder = divmod(c.walltime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        options = ['--export=NIL', '--nodes=1', '--ntasks=1',
            f'--cpus-per-task={c.cpus}', f'--mem={c.memory_mb}M',
            f'--time={hours:02}:{minutes:02}:{seconds:02}', '--job-name=br-' + identifier,
            '--chdir=' + str(directory), '--output=' + str(directory / 'slurm.log'),
            '--error=' + str(directory / 'slurm.log'), '--open-mode=append']
        for key in ('partition', 'account', 'qos', 'constraint'):
            if getattr(c, key):
                options.append('--' + key + '=' + getattr(c, key))
        return options + self.gpu_options()

    def write_script(self, directory):
        path = directory / 'job.sh'
        path.write_text(self.script(directory))
        path.chmod(0o700)
        return path

    def dry_run(self, directory):
        """Ask Slurm to validate the exact request shape without queuing a job."""
        path = self.write_script(directory)
        options = self.batch_options(directory, 'doctor')
        try:
            result = self.run([self.config.sbatch, '--test-only', *options, str(path)])
        except subprocess.CalledProcessError as exc:
            message = ' '.join((exc.stderr or exc.stdout or '').split())[:500]
            raise ValueError('Slurm rejected the dry-run request: ' + (message or 'no message')) from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Slurm dry run timed out') from exc
        # sbatch --test-only reports the estimated start on stderr.
        message = ' '.join((result.stderr or result.stdout or '').split())[:500]
        shown = ('--partition', '--account', '--qos', '--constraint', '--gres', '--gpus',
                 '--cpus-per-task', '--mem', '--time')
        return {'accepted': True, 'message': message,
                'options': [o for o in options if o.split('=')[0] in shown]}

    def submit(self, directory, identifier):
        c = self.config
        path = self.write_script(directory)
        command = [c.sbatch, '--parsable', *self.batch_options(directory, identifier)]
        try:
            result = self.run(command + [str(path)])
        except subprocess.TimeoutExpired as exc:
            raise SubmissionUnknown('Slurm submission timed out; reconciliation is required') from exc
        value = result.stdout.strip()
        if not re.fullmatch(r'[1-9][0-9]*(?:;[A-Za-z0-9_.-]+)?', value):
            raise SubmissionUnknown('Slurm response was ambiguous; reconciliation is required')
        return value.split(';')[0]

    @staticmethod
    def checked_id(value):
        if not isinstance(value, str) or not re.fullmatch('[1-9][0-9]*', value):
            raise ValueError('Invalid Slurm job ID')
        return value

    def state(self, identifier):
        identifier = self.checked_id(identifier)
        now = time.monotonic()
        if now < self._unavailable_until:
            raise self._last_error
        cached = self._states.get(identifier)
        if cached and now < cached[0]:
            return cached[1]
        try:
            state = self._state_uncached(identifier)
        except (OSError, subprocess.SubprocessError) as exc:
            # One scheduler timeout must not become 32 serial timeouts while
            # refreshing the spool. Preserve job state and back off briefly.
            self._last_error = exc
            self._unavailable_until = time.monotonic() + 10
            raise
        self._states[identifier] = (time.monotonic() + 10, state)
        if len(self._states) > 1024:
            self._states = {key: item for key, item in self._states.items() if item[0] > now}
        return state

    def _state_uncached(self, identifier):
        try:
            output = self.run([self.config.squeue, '--noheader', '--jobs=' + identifier,
                               '--format=%T']).stdout.strip()
        except subprocess.CalledProcessError:
            # Some Slurm versions return nonzero once a job leaves squeue.
            # Accounting is still authoritative for the terminal state.
            output = ''
        if output:
            return output.splitlines()[0].split()[0].rstrip('+')
        output = self.run([self.config.sacct, '--noheader', '--parsable2', '--allocations',
                          '--jobs=' + identifier, '--format=JobIDRaw,State']).stdout
        for line in output.splitlines():
            parts = line.split('|')
            if len(parts) >= 2 and parts[0] == identifier:
                return parts[1].split()[0].rstrip('+')
        return 'UNKNOWN'

    def cancel(self, identifier):
        self.run([self.config.scancel, self.checked_id(identifier)])

    def find_submission(self, identifier, created_at):
        """Recover an ambiguous sbatch outcome by its exact unique job name and Unix owner."""
        from .common import job_id
        name = 'br-' + job_id(identifier)
        owner = pwd.getpwuid(os.getuid()).pw_name
        pending = self.run([self.config.squeue, '--noheader', '--user=' + owner,
                           '--name=' + name, '--format=%i|%j']).stdout
        since = datetime.fromtimestamp(created_at - 3600).strftime('%Y-%m-%dT%H:%M:%S')
        history = self.run([self.config.sacct, '--noheader', '--parsable2', '--allocations',
                           '--user=' + owner, '--name=' + name, '--starttime=' + since,
                           '--format=JobIDRaw,JobName%64']).stdout
        matches = set()
        for line in (pending + '\n' + history).splitlines():
            fields = line.split('|')
            if len(fields) >= 2 and fields[1].strip() == name:
                matches.add(self.checked_id(fields[0].strip()))
        if len(matches) > 1:
            raise RuntimeError('Multiple matching Slurm jobs; administrator review required')
        return next(iter(matches), None)
