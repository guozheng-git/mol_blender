---
description: Render the current workspace view in Blender with a preset
argument-hint: [preset ids] [rough | full quality | gif]
---

Use the molblender-studio skill: render the open workspace's current view with $ARGUMENTS.

Follow Recipe C exactly, once per preset requested (default `mint_sky_assembly`). If `studio.py lab` lists exactly one profile, render on the lab GPU (Recipe G) unless the user asked for a local render. If no workspace is open in this chat, find it with Recipe D first, or create one with Recipe A when the user names a structure.
