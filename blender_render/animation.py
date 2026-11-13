"""Shared animation validation, lossless frame transport and CPU-only export."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import zipfile

from .defaults import ANIMATION, ANIMATION_LIMITS

CONTROLS = ('animation', 'duration', 'fps', 'rock_angle', 'gif')
PATH_VIEW_LENGTH = 18  # one PyMOL get_view() per frame
MAX_RGBA = ANIMATION_LIMITS['max_rgba_bytes']
MAX_ARCHIVE = ANIMATION_LIMITS['max_archive_bytes']
MAX_FRAMES = ANIMATION_LIMITS['max_frames']
MIN_FRAMES = ANIMATION_LIMITS['min_frames']
MAX_FPS = ANIMATION_LIMITS['max_fps']
MAX_DURATION = ANIMATION_LIMITS['max_duration']
MAX_ROCK_ANGLE = ANIMATION_LIMITS['max_rock_angle']


def normalize(value, output):
    if not isinstance(value, dict) or set(value) - {'mode', 'duration', 'fps', 'rock_angle', 'gif', 'path', 'states', 'anchor', 'lid'}:
        raise ValueError('Invalid animation fields')
    mode = value.get('mode', 'none')
    if mode not in ('none', 'rock', '360', 'path'):
        raise ValueError('animation must be none, rock, 360 or path')
    if {'path', 'states', 'anchor', 'lid'} & set(value) and mode != 'path':
        raise ValueError('path, states, anchor and lid apply only to mode=path')
    if mode == 'path':
        return _normalize_path(value, output)
    if mode == 'none':
        if set(value) - {'mode'}:
            raise ValueError('Animation controls require rock or 360')
        return None
    if output.get('raw_view'):
        raise ValueError('Animation is incompatible with raw_view')
    fps = value.get('fps', ANIMATION['fps'])
    duration = value.get('duration', ANIMATION['orbits'][mode]['duration'])
    if type(fps) is not int or not 1 <= fps <= MAX_FPS:
        raise ValueError(f'Animation fps must be an integer from 1 to {MAX_FPS}')
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
        raise ValueError(f'Animation duration must be finite, positive and at most {MAX_DURATION} seconds')
    frames = round(fps * duration)
    if not math.isclose(frames, fps * duration, abs_tol=1e-8) or not MIN_FRAMES <= frames <= MAX_FRAMES:
        raise ValueError(f'Animation must contain an integral number of frames from {MIN_FRAMES} to {MAX_FRAMES}')
    if frames * output['width'] * output['height'] * 4 > MAX_RGBA:
        raise ValueError(f'Animation exceeds {MAX_RGBA / 1024**3:g} GiB of decoded RGBA frames; reduce resolution or duration')
    gif = value.get('gif', ANIMATION['gif'])
    if type(gif) is not bool:
        raise ValueError('gif must be a boolean')
    result = dict(mode=mode, duration=duration, fps=fps, gif=gif)
    if mode == 'rock':
        angle = value.get('rock_angle', ANIMATION['orbits']['rock']['rock_angle'])
        if type(angle) not in (int, float) or not math.isfinite(angle) or not 0 < angle <= MAX_ROCK_ANGLE:
            raise ValueError(f'rock_angle must be in (0, {MAX_ROCK_ANGLE}] degrees')
        result['rock_angle'] = angle
    elif 'rock_angle' in value:
        raise ValueError('rock_angle applies only to rock')
    return result


def _normalize_path(value, output):
    """A camera path: one PyMOL view per frame, played at fps.

    The first view is the rendered scene's own camera. Every later frame moves
    the camera by that view's rigid motion relative to the first, so fitted
    framing and lens choices of the preset are kept.
    """
    if output.get('raw_view'):
        raise ValueError('Animation is incompatible with raw_view')
    if set(value) - {'mode', 'path', 'fps', 'duration', 'gif', 'states', 'anchor', 'lid'}:
        raise ValueError('rock_angle applies only to rock')
    fps = value.get('fps', ANIMATION['fps'])
    if type(fps) is not int or not 1 <= fps <= MAX_FPS:
        raise ValueError(f'Animation fps must be an integer from 1 to {MAX_FPS}')
    path = value.get('path')
    if not isinstance(path, list) or not MIN_FRAMES <= len(path) <= MAX_FRAMES:
        raise ValueError(f'Animation path must contain {MIN_FRAMES} to {MAX_FRAMES} views')
    for view in path:
        if (not isinstance(view, list) or len(view) != PATH_VIEW_LENGTH
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in view)):
            raise ValueError('Each animation path view must be 18 finite numbers from PyMOL get_view()')
        if view[11] >= 0:
            raise ValueError('Animation path views must keep the camera in front of the origin')
    duration = len(path) / fps
    if 'duration' in value and (type(value['duration']) not in (int, float)
                                or not math.isfinite(value['duration'])
                                or not math.isclose(value['duration'], duration, abs_tol=1e-8)):
        raise ValueError('Animation duration must equal the path length divided by fps')
    if duration > MAX_DURATION:
        raise ValueError(f'Animation duration must be at most {MAX_DURATION} seconds')
    if len(path) * output['width'] * output['height'] * 4 > MAX_RGBA:
        raise ValueError(f'Animation exceeds {MAX_RGBA / 1024**3:g} GiB of decoded RGBA frames; reduce resolution or duration')
    gif = value.get('gif', ANIMATION['gif'])
    if type(gif) is not bool:
        raise ValueError('gif must be a boolean')
    result = dict(mode='path', duration=duration, fps=fps, gif=gif,
                  path=[[float(v) for v in view] for view in path])
    if 'states' in value:
        # Molecular state shown on each frame, numbered like PyMOL states.
        states = value['states']
        if (not isinstance(states, list) or len(states) != len(path)
                or any(type(n) is not int or not 1 <= n <= MAX_FRAMES for n in states)):
            raise ValueError(f'Animation states must give one PyMOL state number (1-{MAX_FRAMES}) per path view')
        result['states'] = list(states)
    # The anchor view is the rendered scene's camera (and the still image);
    # every other frame moves relative to it. Camera-facing cuts aim from it.
    anchor = value.get('anchor', 0)
    if type(anchor) is not int or not 0 <= anchor < len(path):
        raise ValueError('Animation anchor must index one of the path views')
    result['anchor'] = anchor
    if 'lid' in value:
        # Opacity of the surface a ligand cutout removes, per frame: 1 shows
        # the intact surface, 0 the finished cut.
        lid = value['lid']
        if (not isinstance(lid, list) or len(lid) != len(path)
                or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in lid)):
            raise ValueError('Animation lid must give one opacity from 0 to 1 per path view')
        result['lid'] = [float(v) for v in lid]
    return result


def resolve(options, output):
    value = {} if options.get('animation') == 'none' else dict(output.get('animation', {}))
    for key in CONTROLS:
        if key in options:
            value['mode' if key == 'animation' else key] = options[key]
    result = normalize(value, output)
    if result is None:
        output.pop('animation', None)
    else:
        output['animation'] = result
    return result


def angles(config):
    count = round(config['duration'] * config['fps'])
    if config['mode'] == '360':
        return [360 * i / count for i in range(count)]
    return [config['rock_angle'] * math.sin(2 * math.pi * i / count) for i in range(count)]


def required_names(style):
    output = style.get('output', {})
    names = ['render.png']
    if output.get('save_blend'):
        names.append('render.blend')
    cfg = output.get('animation')
    if cfg and cfg.get('mode') in ('rock', '360', 'path'):
        names += ['render.mp4', 'frames.zip']
        if cfg.get('gif'):
            names.append('render.gif')
    return names


def encoder():
    executable = shutil.which('ffmpeg')
    if not executable:
        raise ValueError('Animation export requires FFmpeg with libx264 on the execution host')
    result = subprocess.run([executable, '-hide_banner', '-encoders'], capture_output=True, text=True,
                            timeout=10, check=True)
    if 'libx264' not in result.stdout or ' gif ' not in result.stdout:
        raise ValueError('FFmpeg requires libx264 and GIF encoders')
    return executable


def _run(command, *, deadline=None, cancelled=None):
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            while process.poll() is None:
                if cancelled and cancelled():
                    raise RuntimeError('Animation encoding cancelled')
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError('Animation execution deadline expired')
                time.sleep(.05)
            if process.returncode:
                log.seek(0)
                raise RuntimeError('FFmpeg failed: ' + log.read(4096).decode(errors='replace'))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def encode(frames, metadata, destination, *, executable=None, deadline=None, cancelled=None):
    destination = Path(destination)
    if destination.suffix.lower() not in ('.mp4', '.gif'):
        raise ValueError('Movie export filename must end in .mp4 or .gif')
    executable = executable or encoder()
    # Composite unpremultiplied PNG alpha onto white. Pad odd MP4 dimensions,
    # preserving every rendered pixel rather than cropping/reframing the image.
    white = 'split[fg][bg];[bg]lutrgb=r=255:g=255:b=255,lut=a=255[white];[white][fg]overlay=shortest=1:format=auto'
    filters = (white + ',pad=ceil(iw/2)*2:ceil(ih/2)*2:color=white[v]' if destination.suffix.lower() == '.mp4'
               else white + ',split[a][b];[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=sierra2_4a[v]')
    # YUV 4:2:0 is intentional: macOS can accept RGB H.264 but decode its
    # colours incorrectly. Keep the original PNG archive for lossless pixels.
    codec = ['-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart'] if destination.suffix.lower() == '.mp4' else ['-loop', '0']
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.movie-export-', dir=destination.parent) as directory:
        temporary = Path(directory) / ('movie' + destination.suffix.lower())
        _run([executable, '-nostdin', '-v', 'error', '-threads', '2', '-filter_complex_threads', '1',
              '-framerate', str(metadata['fps']), '-i', str(Path(frames) / '%04d.png'),
              '-filter_complex', filters, '-map', '[v]', '-frames:v', str(metadata['frames']),
              *codec, str(temporary)], deadline=deadline, cancelled=cancelled)
        temporary.replace(destination)


def package_frames(directory, archive, config, width, height):
    paths = sorted(Path(directory).glob('*.png'))
    metadata = dict(version=1, fps=config['fps'], frames=len(paths), width=width, height=height,
                    animation=config, files=[dict(name=p.name, size=p.stat().st_size,
                        sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths])
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as out:
        out.writestr('frames.json', json.dumps(metadata))
        for path in paths:
            out.write(path, path.name)
    if Path(archive).stat().st_size > MAX_ARCHIVE:
        raise ValueError(f'Frame archive exceeds {MAX_ARCHIVE / 1024**2:g} MiB')
    return metadata


@contextmanager
def unpack(archive):
    """Validate all members and PNG headers before exposing paths to PyMOL/FFmpeg."""
    archive = Path(archive)
    if archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError(f'Frame archive exceeds {MAX_ARCHIVE / 1024**2:g} MiB')
    with tempfile.TemporaryDirectory(prefix='blender-movie-') as directory, zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > MAX_FRAMES + 1 or len({i.filename for i in members}) != len(members):
            raise ValueError('Invalid frame archive member count')
        if sum(i.file_size for i in members) > MAX_ARCHIVE or any(i.is_dir() or i.flag_bits & 1 or (i.external_attr >> 16) & 0o170000 == 0o120000 for i in members):
            raise ValueError('Invalid frame archive sizes or members')
        info = source.getinfo('frames.json')
        if info.file_size > 1024 * 1024:
            raise ValueError('Frame metadata too large')
        meta = json.loads(source.read(info))
        if not isinstance(meta, dict) or meta.get('version') != 1 or any(type(meta.get(k)) is not int for k in ('fps', 'frames', 'width', 'height')):
            raise ValueError('Invalid movie metadata')
        n, w, h = meta['frames'], meta['width'], meta['height']
        if not MIN_FRAMES <= n <= MAX_FRAMES or not 1 <= meta['fps'] <= MAX_FPS or n / meta['fps'] > MAX_DURATION or min(w, h) < 1 or n*w*h*4 > MAX_RGBA:
            raise ValueError('Movie exceeds frame, FPS or memory bounds')
        names = [f'{i:04d}.png' for i in range(n)]
        if {i.filename for i in members} != set(names) | {'frames.json'}:
            raise ValueError('Unexpected frame archive paths')
        records = meta.get('files')
        if not isinstance(records, list) or len(records) != n or any(not isinstance(i, dict) for i in records) or [i.get('name') for i in records] != names:
            raise ValueError('Invalid frame ordering')
        for record in records:
            raw = source.read(record['name'])
            if len(raw) != record.get('size') or hashlib.sha256(raw).hexdigest() != record.get('sha256'):
                raise ValueError('Frame checksum mismatch')
            if len(raw) < 33 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR' or struct.unpack('>II', raw[16:24]) != (w, h):
                raise ValueError('Invalid frame PNG dimensions')
            from PIL import Image
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
            except (OSError, ValueError) as exc:
                raise ValueError('Invalid frame PNG data') from exc
            (Path(directory) / record['name']).write_bytes(raw)
        yield Path(directory), meta


def export(archive, destination, **kwargs):
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise ValueError('Output already exists: ' + str(destination))
    with unpack(archive) as (frames, metadata):
        encode(frames, metadata, destination, **kwargs)
    return str(Path(destination).resolve())
