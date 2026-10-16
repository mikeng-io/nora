"""The presence collector — a SEPARATE process from Nora the agent.

`python -m nora.presence` runs the change-recorder daemon: it reads circle
members from Life360 at a cadence, writes a fix only when a member's position
advances, and absorbs Life360's auth/challenge fragility so Nora reads only the
local `presence_fixes` table. It shares the `nora` database and the Life360
client library with Nora, nothing else, and either can be down without the
other.
"""
