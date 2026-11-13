from __future__ import annotations

from nora.agent.toolkit.spec import _spec


def build(deps):
    from nora.computer.adapters.tools.files import _make_file_fetch

    file_fetch = _make_file_fetch(deps)
    from nora.computer.adapters.tools.sandbox import _make_computer_run

    computer_run = _make_computer_run(deps)
    from nora.computer.adapters.tools.sandbox import _make_computer_read

    computer_read = _make_computer_read(deps)
    from nora.computer.adapters.tools.sandbox import _make_computer_write

    computer_write = _make_computer_write(deps)
    from nora.computer.adapters.tools.sandbox import _make_computer_edit

    computer_edit = _make_computer_edit(deps)
    from nora.computer.adapters.tools.export import _make_computer_export

    computer_export = _make_computer_export(deps)
    from nora.computer.adapters.tools.transfer import _make_computer_import

    computer_import = _make_computer_import(deps)
    from nora.computer.adapters.tools.sessions import _make_computer_session_list

    computer_session_list = _make_computer_session_list(deps)
    from nora.computer.adapters.tools.sessions import _make_computer_browse

    computer_browse = _make_computer_browse(deps)
    from nora.computer.adapters.tools.sessions import _make_computer_capture

    computer_capture = _make_computer_capture(deps)
    from nora.computer.adapters.tools.sessions import _make_computer_cancel

    computer_cancel = _make_computer_cancel(deps)
    return (
        _spec("file_fetch", file_fetch),
        _spec("computer_run", computer_run),
        _spec("computer_read", computer_read),
        _spec("computer_write", computer_write),
        _spec("computer_edit", computer_edit),
        _spec("computer_export", computer_export),
        _spec("computer_import", computer_import),
        _spec("computer_session_list", computer_session_list),
        _spec("computer_browse", computer_browse),
        _spec("computer_capture", computer_capture),
        _spec("computer_cancel", computer_cancel),
    )
