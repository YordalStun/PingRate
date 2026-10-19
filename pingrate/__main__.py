import sys


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    from . import icmp
    demo = "--demo" in argv
    if demo:
        icmp.set_simulation(True)
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from .settings import Settings
    from .ui import theme
    from .ui.main_window import MainWindow
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(theme.QSS)
    s = Settings.load()
    if demo:
        s.targets = ["demo-ok.example", "demo-outage.example"]
        s.save = lambda: None
    w = MainWindow(s)
    w.show()
    if "--shot" in argv:                      # headless screenshot for testing
        from PySide6.QtCore import QTimer
        path = argv[argv.index("--shot") + 1]
        delay = int(argv[argv.index("--shot") + 2]) if len(argv) > argv.index("--shot") + 2 else 6000
        QTimer.singleShot(delay, lambda: (w.grab().save(path), app.quit()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
