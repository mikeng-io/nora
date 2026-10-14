"""Shared-file provider adapters (design/external-files/00-design.md §4).

One module per provider behind `nora.domain.ports.ExternalFilesSource`
(`google_drive`), and `text`, which reads a downloaded file's bytes as text
for any of them. Nothing here keeps what it read.
"""
