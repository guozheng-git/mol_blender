"""Fixed administrator-selected Slurm/Apptainer commands; never accept shell input."""
import os
import re
import shlex
import subprocess


class SubmissionUnknown(RuntimeError):
    """A job may have been accepted. Never resubmit automatically."""


class Slurm:
    def __init__(self, config):
        self.config = config

    def run(self, command):
        # Do not inherit SBATCH_*, APPTAINER_*, Python paths or service credentials.
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
        for name in ('HOME', 'USER', 'LOGNAME', 'SLURM_CONF'):
            if name in os.environ:
                env[name] = os.environ[name]
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=30, check=True)

    def script(self, directory):
        c = self.config
        command = [c.apptainer, 'exec', '--cleanenv', '--containall', '--no-home',
                   '--no-mount', 'hostfs,bind-paths,cwd', '--bind', f'{directory}:/job',
                   '--pwd', '/job']
        if c.gpus:
            command.append('--nv')
        command.extend([str(c.image), '/opt/studio-runtime/render/bin/python', '-I',
                        '-m', 'blender_render.cluster.worker', '/job'])
        return '#!/bin/sh\nset -eu\numask 077\nexec ' + shlex.join(command) + '\n'

    def submit(self, directory, identifier):
        c = self.config
        path = directory / 'job.sh'
        path.write_text(self.script(directory))
        path.chmod(0o700)
        hours, remainder = divmod(c.walltime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        command = [c.sbatch, '--parsable', '--export=NIL', '--nodes=1', '--ntasks=1',
            f'--cpus-per-task={c.cpus}', f'--mem={c.memory_mb}M',
            f'--time={hours:02}:{minutes:02}:{seconds:02}', '--job-name=br-' + identifier,
            '--chdir=' + str(directory), '--output=' + str(directory / 'slurm.log'),
            '--error=' + str(directory / 'slurm.log'), '--open-mode=append']
        for key in ('partition', 'account', 'qos'):
            if getattr(c, key):
                command.append('--' + key + '=' + getattr(c, key))
        if c.gpus:
            command.append('--gpus=1')
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
        output = self.run([self.config.squeue, '--noheader', '--jobs=' + identifier,
                           '--format=%T']).stdout.strip()
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
