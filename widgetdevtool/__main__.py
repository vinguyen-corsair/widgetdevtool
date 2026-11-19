from __future__ import annotations

import sys

# QtWebEngine modules must be imported before QApplication is created.
from PySide6.QtCore import Qt
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PySide6.QtWidgets import QApplication

from .instance_storage import default_data_dir
from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(argv)

    window = MainWindow(data_dir=default_data_dir())  # own data folder: instance storage + workspace.json
    window.show()
    if window.workspace_path.is_file():
        try:
            window.restore_workspace()
        except (OSError, ValueError) as exc:
            window.status.setText(f"Workspace not restored: {exc}")

    # Optional: python -m widgetdevtool <widget folder | package.icuewidget>
    if len(argv) > 1:
        if argv[1].lower().endswith(".icuewidget"):
            window.open_package(argv[1])  # extracted into the widgets folder (asks first), or the existing source is opened
        else:
            window.open_folder(argv[1])  # reuses the instance of that folder from the workspace (no duplicate)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())